"""
tests.unit.test_ledger_pause_resume
────────────────────────────────────
RV3-001: Resumable interrupt runtime primitive — ledger layer.

Covers ledger-level pause/resume semantics:
- IN_PROGRESS → PAUSED transition with payload persistence
- PAUSED → IN_PROGRESS resume transition
- resume_count increments
- get_next(include_paused=True)
- reset_in_progress() leaves PAUSED tasks alone (crash recovery safety)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.exceptions import InvalidTransition, TaskNotPaused
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger


@pytest.fixture
def ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )


def _claimed_task(ledger: TaskLedger, title: str = "t1", runner_id: str = "run1") -> Task:
    """Helper: add a task and transition it to IN_PROGRESS."""
    task = Task(title=title, verifier_id="schema", verifier_config={"required_fields": []})
    ledger.add([task])
    return ledger.claim(task.id, runner_id)


class TestLedgerPause:
    def test_pause_transitions_in_progress_to_paused(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        paused = ledger.pause(task.id, reason="needs review", payload={"cursor": {"turn": 2}})

        assert paused.status == TaskStatus.PAUSED
        assert paused.result is not None
        assert paused.result.extras["pause_payload"]["reason"] == "needs review"
        assert paused.result.extras["pause_payload"]["cursor"] == {"turn": 2}
        assert paused.result.extras["pause_payload"]["resume_count"] == 0
        assert "paused_at" in paused.result.extras["pause_payload"]

    def test_pause_clears_claimed_by(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger, runner_id="runA")
        assert task.claimed_by == "runA"
        paused = ledger.pause(task.id, reason="x")
        assert paused.claimed_by is None

    def test_pause_merges_with_existing_result(self, ledger: TaskLedger) -> None:
        """If a task already has a TaskResult (e.g. from checkpoint_result), pause
        must append to it rather than overwrite."""
        task = _claimed_task(ledger)
        existing = TaskResult(raw_output="partial", extras={"prm_checkpoint": {"foo": "bar"}})
        ledger.checkpoint_result(task.id, existing)

        paused = ledger.pause(task.id, reason="pause after partial work")

        assert paused.result is not None
        assert paused.result.raw_output == "partial"
        assert paused.result.extras["prm_checkpoint"] == {"foo": "bar"}
        assert paused.result.extras["pause_payload"]["reason"] == "pause after partial work"

    def test_pause_rejects_non_in_progress_task(self, ledger: TaskLedger) -> None:
        """PENDING → PAUSED is not a valid transition per the state machine."""
        task = Task(title="t1", verifier_id="schema", verifier_config={"required_fields": []})
        ledger.add([task])
        with pytest.raises(InvalidTransition):
            ledger.pause(task.id, reason="x")

    def test_pause_payload_is_atomic_on_disk(self, ledger: TaskLedger) -> None:
        """After pause(), a fresh TaskLedger reading the same file sees PAUSED."""
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="survive restart", payload={"cursor": {"turn": 5}})

        reloaded = TaskLedger(path=ledger.path, progress_file=str(ledger.progress_path))
        fetched = reloaded.get(task.id)
        assert fetched.status == TaskStatus.PAUSED
        assert fetched.result.extras["pause_payload"]["cursor"] == {"turn": 5}


class TestLedgerResume:
    def test_resume_transitions_paused_to_in_progress(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="x")

        resumed = ledger.resume(task.id, runner_id="run2")

        assert resumed.status == TaskStatus.IN_PROGRESS
        assert resumed.claimed_by == "run2"
        assert resumed.result.extras["pause_payload"]["resume_count"] == 1

    def test_resume_twice_increments_count(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="x")
        ledger.resume(task.id, "runA")
        ledger.pause(task.id, reason="y")
        resumed = ledger.resume(task.id, "runB")
        assert resumed.result.extras["pause_payload"]["resume_count"] == 2

    def test_resume_preserves_pause_payload_cursor(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="x", payload={"cursor": {"turn": 7}})
        resumed = ledger.resume(task.id, "runX")
        assert resumed.result.extras["pause_payload"]["cursor"] == {"turn": 7}

    def test_resume_raises_when_task_not_paused(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        with pytest.raises(TaskNotPaused) as exc_info:
            ledger.resume(task.id, "runA")
        assert exc_info.value.status == "in_progress"


class TestResetInProgressLeavesPausedUntouched:
    def test_crash_recovery_does_not_reset_paused(self, ledger: TaskLedger) -> None:
        """RV3-001 critical invariant: reset_in_progress() must preserve PAUSED
        tasks. Otherwise a legitimate pause would be destroyed on next startup."""
        # Two tasks: one IN_PROGRESS (to be reset), one PAUSED (must survive)
        in_progress = _claimed_task(ledger, title="will_reset")
        paused = _claimed_task(ledger, title="will_survive")
        ledger.pause(paused.id, reason="keep me")

        count = ledger.reset_in_progress()

        assert count == 1  # only the IN_PROGRESS task was reset
        assert ledger.get(in_progress.id).status == TaskStatus.PENDING
        assert ledger.get(paused.id).status == TaskStatus.PAUSED
        assert ledger.get(paused.id).result.extras["pause_payload"]["reason"] == "keep me"


class TestGetNextIncludesPaused:
    def test_get_next_default_excludes_paused(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="x")
        assert ledger.get_next() is None

    def test_get_next_with_include_paused_returns_paused_task(self, ledger: TaskLedger) -> None:
        task = _claimed_task(ledger)
        ledger.pause(task.id, reason="x")
        result = ledger.get_next(include_paused=True)
        assert result is not None
        assert result.id == task.id
        assert result.status == TaskStatus.PAUSED

    def test_get_next_prefers_paused_over_pending_with_include_paused(
        self, ledger: TaskLedger
    ) -> None:
        """Paused tasks should be surfaced first so they can resume before new
        pending work starts — ensures HITL approvals aren't starved."""
        pending = Task(title="new", verifier_id="schema", verifier_config={"required_fields": []})
        ledger.add([pending])
        paused_task = _claimed_task(ledger, title="resume_me")
        ledger.pause(paused_task.id, reason="x")

        result = ledger.get_next(include_paused=True)
        assert result is not None
        assert result.id == paused_task.id


class TestTaskStatusPausedEnum:
    def test_paused_is_valid_task_status(self) -> None:
        assert TaskStatus.PAUSED.value == "paused"

    def test_paused_is_not_terminal(self) -> None:
        assert TaskStatus.PAUSED.is_terminal is False

    def test_in_progress_can_transition_to_paused(self) -> None:
        assert TaskStatus.IN_PROGRESS.can_transition_to(TaskStatus.PAUSED)

    def test_paused_can_transition_to_in_progress(self) -> None:
        assert TaskStatus.PAUSED.can_transition_to(TaskStatus.IN_PROGRESS)

    def test_pending_cannot_transition_directly_to_paused(self) -> None:
        assert not TaskStatus.PENDING.can_transition_to(TaskStatus.PAUSED)

    def test_paused_cannot_transition_to_pending(self) -> None:
        """PAUSED must resume via IN_PROGRESS — going back to PENDING would
        lose the pause payload."""
        assert not TaskStatus.PAUSED.can_transition_to(TaskStatus.PENDING)
