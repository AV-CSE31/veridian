"""
tests.integration.test_pause_resume
────────────────────────────────────
RV3-001 + RV3-002 end-to-end coverage.

Proves that:
- A hook raising TaskPauseRequested / HumanReviewRequired transitions the
  task to PAUSED (not FAILED).
- PAUSED state survives a fresh VeridianRunner / TaskLedger instance.
- reset_in_progress() on startup preserves the PAUSED state.
- A second runner picks up the PAUSED task via get_next(include_paused=True)
  and completes it.
- TaskPaused and TaskResumed events are delivered to observability hooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.events import TaskPaused, TaskResumed
from veridian.core.exceptions import TaskPauseRequested
from veridian.core.task import Task, TaskStatus
from veridian.hooks.base import BaseHook
from veridian.hooks.builtin.human_review import HumanReviewHook
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

# ── Fixtures ──────────────────────────────────────────────────────────────────


_SCHEMA_CONFIG = {"required_fields": ["summary"]}


@pytest.fixture
def config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )


@pytest.fixture
def ledger(config: VeridianConfig) -> TaskLedger:
    return TaskLedger(
        path=config.ledger_file,
        progress_file=str(config.progress_file),
    )


def _passing_provider() -> MockProvider:
    provider = MockProvider()
    # Enough scripted responses for both first run and resume.
    for _ in range(10):
        provider.script_veridian_result({"summary": "done"})
    return provider


def _make_task(title: str, **kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "title": title,
        "verifier_id": "schema",
        "verifier_config": _SCHEMA_CONFIG,
    }
    defaults.update(kwargs)
    return Task(**defaults)


# ── Spy hook for event assertions ────────────────────────────────────────────


class _EventSpy(BaseHook):
    id: ClassVar[str] = "event_spy"
    priority: ClassVar[int] = 100  # runs after raising hooks

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def before_task(self, event: Any) -> None:
        self.events.append(("before_task", event))

    def after_task(self, event: Any) -> None:
        self.events.append(("after_task", event))

    def on_pause(self, event: Any) -> None:  # optional lifecycle hook
        self.events.append(("on_pause", event))

    def on_resume(self, event: Any) -> None:
        self.events.append(("on_resume", event))


# ── RV3-001 core tests ────────────────────────────────────────────────────────


class TestPauseOnControlFlowSignal:
    def test_human_review_pauses_task_not_fails_it(
        self, ledger: TaskLedger, config: VeridianConfig
    ) -> None:
        task = _make_task("needs review", metadata={"requires_human_review": True})
        ledger.add([task])

        hooks = HookRegistry()
        hooks.register(HumanReviewHook())
        runner = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        )

        summary = runner.run()

        stored = ledger.get(task.id)
        assert stored.status == TaskStatus.PAUSED, (
            f"Expected PAUSED but got {stored.status}; HITL path is a no-op."
        )
        assert stored.result is not None
        pause_payload = stored.result.extras.get("pause_payload", {})
        assert pause_payload["reason"].startswith("Human review required")
        assert summary.done_count == 0
        assert summary.failed_count == 0

    def test_generic_task_pause_requested_pauses_task(
        self, ledger: TaskLedger, config: VeridianConfig
    ) -> None:
        class CustomPauseHook(BaseHook):
            id: ClassVar[str] = "custom_pause"

            def before_task(self, event: Any) -> None:
                task = getattr(event, "task", None)
                if task and task.metadata.get("custom_pause"):
                    raise TaskPauseRequested(
                        task_id=task.id,
                        reason="custom business rule",
                        payload={"cursor": {"turn": 0}, "resume_hint": "retry after 5m"},
                    )

        task = _make_task("custom", metadata={"custom_pause": True})
        ledger.add([task])

        hooks = HookRegistry()
        hooks.register(CustomPauseHook())
        runner = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        )
        runner.run()

        stored = ledger.get(task.id)
        assert stored.status == TaskStatus.PAUSED
        assert stored.result.extras["pause_payload"]["reason"] == "custom business rule"
        assert stored.result.extras["pause_payload"]["cursor"] == {"turn": 0}
        assert stored.result.extras["pause_payload"]["resume_hint"] == "retry after 5m"


class TestPauseAcrossRunnerRestart:
    def test_paused_task_survives_fresh_runner_and_ledger(
        self, tmp_path: Path, config: VeridianConfig
    ) -> None:
        # Run 1: ledger A, runner A — task gets paused
        ledger_a = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        task = _make_task("pause_me", metadata={"requires_human_review": True})
        ledger_a.add([task])

        hooks_a = HookRegistry()
        hooks_a.register(HumanReviewHook())
        runner_a = VeridianRunner(
            ledger=ledger_a,
            provider=_passing_provider(),
            config=config,
            hooks=hooks_a,
        )
        runner_a.run()

        # Simulate process restart: fresh TaskLedger reading the SAME file
        ledger_b = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        reloaded = ledger_b.get(task.id)
        assert reloaded.status == TaskStatus.PAUSED, (
            "Pause payload did not survive the simulated restart"
        )

        # reset_in_progress must NOT touch PAUSED
        reset_count = ledger_b.reset_in_progress()
        assert reset_count == 0
        assert ledger_b.get(task.id).status == TaskStatus.PAUSED

    def test_second_runner_resumes_and_completes_task(
        self, tmp_path: Path, config: VeridianConfig
    ) -> None:
        # Run 1: pause via HumanReviewHook
        ledger_a = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        task = _make_task("resume_me", metadata={"requires_human_review": True})
        ledger_a.add([task])

        hooks_pause = HookRegistry()
        hooks_pause.register(HumanReviewHook())
        VeridianRunner(
            ledger=ledger_a,
            provider=_passing_provider(),
            config=config,
            hooks=hooks_pause,
        ).run()
        assert ledger_a.get(task.id).status == TaskStatus.PAUSED

        # Run 2: simulate human approval by removing the flag, then resume.
        # The second runner has no HumanReviewHook (approval granted).
        ledger_b = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        stored = ledger_b.get(task.id)
        stored.metadata["requires_human_review"] = False
        # Persist the metadata mutation by re-adding (add() upserts when
        # skip_duplicates=False)
        ledger_b.add([stored], skip_duplicates=False)

        runner_b = VeridianRunner(
            ledger=ledger_b,
            provider=_passing_provider(),
            config=config,
            hooks=HookRegistry(),
        )
        summary = runner_b.run()

        assert summary.done_count == 1
        final = ledger_b.get(task.id)
        assert final.status == TaskStatus.DONE
        # resume_count must reflect that the task was resumed once.
        assert final.result.extras.get("pause_payload", {}).get("resume_count") == 1


class TestPauseResumeEvents:
    def test_pause_fires_task_paused_event(
        self, ledger: TaskLedger, config: VeridianConfig
    ) -> None:
        task = _make_task("paused", metadata={"requires_human_review": True})
        ledger.add([task])

        spy = _EventSpy()
        hooks = HookRegistry()
        hooks.register(HumanReviewHook())
        hooks.register(spy)
        VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        ).run()

        pause_events = [e for name, e in spy.events if name == "on_pause"]
        assert len(pause_events) == 1
        assert isinstance(pause_events[0], TaskPaused)
        assert pause_events[0].reason.startswith("Human review required")
        assert pause_events[0].task is not None
        assert pause_events[0].task.status == TaskStatus.PAUSED

    def test_resume_fires_task_resumed_event(self, tmp_path: Path, config: VeridianConfig) -> None:
        ledger_a = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        task = _make_task("rr", metadata={"requires_human_review": True})
        ledger_a.add([task])

        hooks_a = HookRegistry()
        hooks_a.register(HumanReviewHook())
        VeridianRunner(
            ledger=ledger_a,
            provider=_passing_provider(),
            config=config,
            hooks=hooks_a,
        ).run()

        # Approve + resume
        ledger_b = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        stored = ledger_b.get(task.id)
        stored.metadata["requires_human_review"] = False
        ledger_b.add([stored], skip_duplicates=False)

        spy = _EventSpy()
        hooks_b = HookRegistry()
        hooks_b.register(spy)
        VeridianRunner(
            ledger=ledger_b,
            provider=_passing_provider(),
            config=config,
            hooks=hooks_b,
        ).run()

        resume_events = [e for name, e in spy.events if name == "on_resume"]
        assert len(resume_events) == 1
        assert isinstance(resume_events[0], TaskResumed)
        assert resume_events[0].resume_count == 1


class TestPauseResumeControlFlowEdges:
    def test_on_resume_control_flow_signal_repauses_task(
        self, ledger: TaskLedger, config: VeridianConfig
    ) -> None:
        """RV3-002: Control-flow signals from on_resume must be routed through
        ledger.pause() and never crash the run."""

        task = _make_task("resume-pause-edge")
        ledger.add([task])
        claimed = ledger.claim(task.id, runner_id="seed-run")
        ledger.pause(claimed.id, reason="seed pause")

        class PauseOnResumeHook(BaseHook):
            id: ClassVar[str] = "pause_on_resume"

            def on_resume(self, event: Any) -> None:
                resumed_task = getattr(event, "task", None)
                if resumed_task is None:
                    return
                raise TaskPauseRequested(
                    task_id=resumed_task.id,
                    reason="pause requested during on_resume",
                    payload={"resume_hint": "needs manual check"},
                )

        hooks = HookRegistry()
        hooks.register(PauseOnResumeHook())
        summary = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        ).run()

        stored = ledger.get(task.id)
        assert summary.done_count == 0
        assert summary.failed_count == 0
        assert stored.status == TaskStatus.PAUSED
        assert stored.result is not None
        assert stored.result.extras["pause_payload"]["reason"] == "pause requested during on_resume"

    def test_repeatedly_paused_task_does_not_starve_other_paused_tasks(
        self, ledger: TaskLedger, config: VeridianConfig
    ) -> None:
        """RV3-001 hardening: if paused task A keeps pausing, paused task B
        should still be resumed and processed in the same run."""

        task_a = _make_task("A", priority=100, metadata={"pause_on_dispatch": True})
        task_b = _make_task("B", priority=50, metadata={"pause_on_dispatch": False})
        ledger.add([task_a, task_b])

        for task_id in (task_a.id, task_b.id):
            claimed = ledger.claim(task_id, runner_id="seed-run")
            ledger.pause(claimed.id, reason="seed pause")

        class PauseTaskAOnBeforeTaskHook(BaseHook):
            id: ClassVar[str] = "pause_task_a_before_task"

            def before_task(self, event: Any) -> None:
                task = getattr(event, "task", None)
                if task and task.metadata.get("pause_on_dispatch"):
                    raise TaskPauseRequested(task_id=task.id, reason="repeat pause for task A")

        hooks = HookRegistry()
        hooks.register(PauseTaskAOnBeforeTaskHook())
        summary = VeridianRunner(
            ledger=ledger,
            provider=_passing_provider(),
            config=config,
            hooks=hooks,
        ).run()

        final_a = ledger.get(task_a.id)
        final_b = ledger.get(task_b.id)
        assert summary.done_count == 1
        assert summary.failed_count == 0
        assert final_a.status == TaskStatus.PAUSED
        assert final_b.status == TaskStatus.DONE
