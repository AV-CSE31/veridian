"""
tests.unit.test_run_abort_signal
---------------------------------------------------------------
Fitment follow-up item 1: ``CostLimitExceeded`` must halt the run, not
get swallowed and not pause a single task. The contract is enforced by
``RunAbortRequested`` — a new ``ControlFlowSignal`` subclass that the
dispatcher catches in the outer loop and translates into shutdown +
``RunSummary.errors`` instead of per-task pause.
"""

from __future__ import annotations

import pytest

from veridian.core.events import TaskClaimed
from veridian.core.exceptions import (
    ControlFlowSignal,
    CostLimitExceeded,
    RepetitionDetected,
    RunAbortRequested,
    WallClockBudgetExceeded,
)
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry


class TestSignalHierarchy:
    def test_run_abort_is_control_flow(self) -> None:
        exc = RunAbortRequested("test", source="t")
        assert isinstance(exc, ControlFlowSignal)

    def test_cost_limit_is_run_abort(self) -> None:
        exc = CostLimitExceeded(current=2.0, limit=1.0)
        assert isinstance(exc, RunAbortRequested)
        assert isinstance(exc, ControlFlowSignal)
        assert exc.source == "cost_guard"
        assert "2" in str(exc) and "1" in str(exc)

    def test_wall_clock_is_run_abort(self) -> None:
        exc = WallClockBudgetExceeded(elapsed=30.0, limit=10.0)
        assert isinstance(exc, RunAbortRequested)
        assert exc.source == "wall_clock_budget"

    def test_repetition_is_run_abort(self) -> None:
        exc = RepetitionDetected(window=3, fingerprint="abcd1234" * 4)
        assert isinstance(exc, RunAbortRequested)
        assert exc.window == 3


class TestPropagatesThroughRegistry:
    def test_run_abort_escapes_hook_registry(self) -> None:
        class AbortingHook(BaseHook):
            id = "abort"

            def before_task(self, event: object) -> None:
                raise RunAbortRequested(reason="stop", source="t")

        reg = HookRegistry()
        reg.register(AbortingHook())
        with pytest.raises(RunAbortRequested) as exc_info:
            reg.fire("before_task", TaskClaimed(run_id="r1"))
        assert exc_info.value.source == "t"
        assert "stop" in str(exc_info.value)

    def test_cost_limit_exceeded_escapes_hook_registry(self) -> None:
        class CostHook(BaseHook):
            id = "cost"

            def before_task(self, event: object) -> None:
                raise CostLimitExceeded(current=5.0, limit=1.0)

        reg = HookRegistry()
        reg.register(CostHook())
        with pytest.raises(CostLimitExceeded):
            reg.fire("before_task", TaskClaimed(run_id="r1"))


class TestDispatcherHaltsOnRunAbort:
    """Integration-ish: when CostGuardHook trips, the dispatcher halts."""

    def test_cost_guard_breach_halts_run(self, tmp_path) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.core.task import Task
        from veridian.hooks.builtin.cost_guard import CostGuardHook
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import VeridianRunner
        from veridian.providers.mock_provider import MockProvider

        # Pre-seed CostGuardHook past its budget so it raises on the
        # FIRST before_task, before any worker runs. Verifies the
        # dispatcher catches RunAbortRequested in the outer loop and
        # records "run_aborted" in summary.errors.
        config = VeridianConfig(
            max_turns_per_task=2,
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(
            path=config.ledger_file,
            progress_file=str(config.progress_file),
        )
        ledger.add(
            [
                Task(title="trivial", verifier_id="schema", verifier_config={}),
                Task(title="never-claimed", verifier_id="schema", verifier_config={}),
            ]
        )

        hooks = HookRegistry()
        guard = CostGuardHook(max_cost_usd=0.01)
        guard._current_cost = 1.0  # already over budget
        hooks.register(guard)

        runner = VeridianRunner(
            ledger=ledger,
            provider=MockProvider(),
            hooks=hooks,
            config=config,
        )
        summary = runner.run()
        assert any("run_aborted" in e for e in summary.errors), summary.errors
        assert any("cost_guard" in e for e in summary.errors), summary.errors
        # The second task was never claimed because the run halted.
        assert summary.done_count == 0
