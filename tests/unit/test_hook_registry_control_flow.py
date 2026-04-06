"""
tests.unit.test_hook_registry_control_flow
───────────────────────────────────────────
RV3-002: Control-flow hook channel hardening.

HookRegistry.fire() must distinguish observability errors (caught & logged)
from control-flow signals (propagated to the runner). Without this split,
HITL pause-and-resume is impossible because HumanReviewRequired would be
swallowed before the runner ever saw it.
"""

from __future__ import annotations

import pytest

from veridian.core.events import TaskClaimed
from veridian.core.exceptions import (
    ControlFlowSignal,
    HumanReviewRequired,
    TaskPauseRequested,
    VeridianError,
)
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry


class _RaisingHook(BaseHook):
    """Test helper that raises a configured exception from before_task."""

    id = "raising"

    def __init__(self, exc: BaseException, priority: int = 50) -> None:
        self._exc = exc
        type(self).priority = priority  # ClassVar override for the instance's class

    def before_task(self, event: object) -> None:
        raise self._exc


class _SpyHook(BaseHook):
    """Test helper that records that it was called."""

    id = "spy"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def before_task(self, event: object) -> None:
        self.calls.append("before_task")


class TestControlFlowSignalPropagation:
    def test_observability_exception_is_swallowed(self) -> None:
        """Non-ControlFlowSignal exceptions remain swallowed (backward compat)."""

        class BrokenHook(BaseHook):
            id = "broken"

            def before_task(self, event: object) -> None:
                raise RuntimeError("observability failure")

        reg = HookRegistry()
        reg.register(BrokenHook())
        # Must not raise — run continues
        reg.fire("before_task", TaskClaimed(run_id="r1"))

    def test_control_flow_signal_propagates(self) -> None:
        """TaskPauseRequested must escape HookRegistry.fire() so runner can act."""

        class PausingHook(BaseHook):
            id = "pausing"

            def before_task(self, event: object) -> None:
                raise TaskPauseRequested(task_id="t1", reason="test pause")

        reg = HookRegistry()
        reg.register(PausingHook())
        with pytest.raises(TaskPauseRequested) as exc_info:
            reg.fire("before_task", TaskClaimed(run_id="r1"))
        assert exc_info.value.task_id == "t1"
        assert "test pause" in str(exc_info.value)

    def test_human_review_required_propagates_as_control_flow(self) -> None:
        """HumanReviewRequired is a ControlFlowSignal subclass and must propagate."""

        class ReviewHook(BaseHook):
            id = "review"

            def before_task(self, event: object) -> None:
                raise HumanReviewRequired(task_id="t1", reason="needs approval")

        reg = HookRegistry()
        reg.register(ReviewHook())
        with pytest.raises(HumanReviewRequired):
            reg.fire("before_task", TaskClaimed(run_id="r1"))

    def test_human_review_required_is_control_flow_signal(self) -> None:
        """Backward-compat: HumanReviewRequired must still be a VeridianError
        AND now also a ControlFlowSignal."""
        exc = HumanReviewRequired(task_id="t1", reason="x")
        assert isinstance(exc, VeridianError)
        assert isinstance(exc, ControlFlowSignal)

    def test_first_control_flow_signal_wins(self) -> None:
        """When a control-flow signal fires, subsequent hooks are skipped."""

        class PausingHook(BaseHook):
            id = "pausing"
            priority = 10

            def before_task(self, event: object) -> None:
                raise TaskPauseRequested(task_id="t1", reason="stop")

        spy = _SpyHook()
        type(spy).priority = 50  # runs after PausingHook

        reg = HookRegistry()
        reg.register(PausingHook())
        reg.register(spy)
        with pytest.raises(TaskPauseRequested):
            reg.fire("before_task", TaskClaimed(run_id="r1"))
        assert spy.calls == [], "Spy hook must not run after a control-flow signal"

    def test_observability_error_does_not_block_subsequent_hooks(self) -> None:
        """A RuntimeError in hook A must not prevent hook B from running."""

        class BrokenA(BaseHook):
            id = "broken_a"
            priority = 10

            def before_task(self, event: object) -> None:
                raise ValueError("observability crash")

        spy = _SpyHook()
        type(spy).priority = 50

        reg = HookRegistry()
        reg.register(BrokenA())
        reg.register(spy)
        reg.fire("before_task", TaskClaimed(run_id="r1"))  # must not raise
        assert spy.calls == ["before_task"]

    def test_task_pause_requested_carries_payload(self) -> None:
        """TaskPauseRequested payload survives propagation through the registry."""

        payload = {"cursor": {"turn": 3}, "resume_hint": "retry with extra context"}

        class PausingHook(BaseHook):
            id = "pausing"

            def before_task(self, event: object) -> None:
                raise TaskPauseRequested(task_id="t42", reason="need data", payload=payload)

        reg = HookRegistry()
        reg.register(PausingHook())
        with pytest.raises(TaskPauseRequested) as exc_info:
            reg.fire("before_task", TaskClaimed(run_id="r1"))
        assert exc_info.value.payload == payload
        assert exc_info.value.payload["cursor"]["turn"] == 3
