"""
tests.integration.test_checkpoint_cursor_resume
─────────────────────────────────────────────────
WCP-011 acceptance: injected-crash tests at multiple step boundaries
prove deterministic resume from the exact cursor position.

These tests exercise the runner + checkpoint cursor end-to-end:
1. Drive a task through N logical steps with the cursor advancing.
2. Simulate a crash at step K (K ∈ {1, 2, 3}).
3. Resume — assert the cursor was stamped at K-1, no duplicate
   activity re-execution, and the task continues from step K onward.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult, TraceStep
from veridian.ledger.ledger import TaskLedger
from veridian.loop.activity import ActivityJournal, ActivityRecord
from veridian.loop.checkpoint_cursor import (
    advance_cursor,
    is_step_completed,
    load_cursor,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )


def _seed_claimed_task(ledger: TaskLedger, task_id: str = "t1") -> Task:
    task = Task(
        id=task_id,
        title="cursor_test",
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return ledger.claim(task.id, "run-1")


def _mk_step(idx: int, step_id: str) -> TraceStep:
    return TraceStep(
        step_id=step_id,
        role="assistant",
        action_type="reason",
        content=f"step {idx}",
        timestamp_ms=idx * 1000,
    )


# ── Simulated multi-step workflow ─────────────────────────────────────────────


_STEPS: list[tuple[str, str]] = [
    ("step_plan", "llm_complete:t1:a0:t0"),
    ("step_reason", "llm_complete:t1:a0:t1"),
    ("step_verify", "llm_complete:t1:a0:t2"),
    ("step_finalize", "llm_complete:t1:a0:t3"),
]


def _execute_until_crash(
    result: TaskResult,
    journal: ActivityJournal,
    *,
    task_id: str,
    crash_at_step: int,
) -> None:
    """Simulate a workflow that runs steps 0..crash_at_step-1 and then
    'crashes' (raises RuntimeError). Each step advances the cursor AND
    records an activity journal entry AND appends a trace step — mimics
    what the real runner + worker pipeline does per turn."""
    for idx, (step_id, activity_key) in enumerate(_STEPS):
        if idx == crash_at_step:
            raise RuntimeError(f"injected crash at step {idx} ({step_id})")
        result.trace_steps.append(_mk_step(idx, step_id))
        journal.append(
            ActivityRecord(
                activity_id=f"act_{idx:03d}",
                idempotency_key=activity_key,
                fn_name="provider.complete",
                args_hash="h",
                result={"content": f"output_{idx}"},
                attempts=1,
                status="success",
                timestamp_ms=idx * 1000,
            )
        )
        advance_cursor(
            result=result,
            task_id=task_id,
            step_id=step_id,
            activity_key=activity_key,
            state={"model_id": "mock/v1"},
        )


def _resume_from_cursor(result: TaskResult, journal: ActivityJournal, task_id: str) -> list[str]:
    """Replay the workflow from the cursor. Returns the list of step_ids
    actually executed during resume (i.e., NOT skipped)."""
    cursor = load_cursor(result)
    resumed_from_idx = (cursor.step_index + 1) if cursor is not None else 0
    executed: list[str] = []
    for idx, (step_id, activity_key) in enumerate(_STEPS):
        if idx < resumed_from_idx:
            # Skip: already completed. Verify via journal + cursor.
            assert journal.get(activity_key) is not None, f"step {idx} should be in journal"
            continue
        result.trace_steps.append(_mk_step(idx, step_id))
        journal.append(
            ActivityRecord(
                activity_id=f"act_{idx:03d}_resumed",
                idempotency_key=activity_key,
                fn_name="provider.complete",
                args_hash="h",
                result={"content": f"output_{idx}"},
                attempts=1,
                status="success",
                timestamp_ms=(idx + 100) * 1000,
            )
        )
        advance_cursor(
            result=result,
            task_id=task_id,
            step_id=step_id,
            activity_key=activity_key,
            state={"model_id": "mock/v1"},
        )
        executed.append(step_id)
    return executed


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCrashAtStepBoundaries:
    """Inject crashes at 3+ step boundaries (1, 2, 3) and prove resume
    starts from the exact cursor position. This is the WCP-011
    acceptance requirement."""

    @pytest.mark.parametrize("crash_at", [1, 2, 3])
    def test_crash_at_step_resumes_from_cursor(self, ledger: TaskLedger, crash_at: int) -> None:
        task = _seed_claimed_task(ledger)
        result = TaskResult(raw_output="")
        journal = ActivityJournal()

        # Pass 1: run until crash
        with pytest.raises(RuntimeError, match=f"crash at step {crash_at}"):
            _execute_until_crash(result, journal, task_id=task.id, crash_at_step=crash_at)

        # Persist state (what the runner would do after catching the crash)
        result.extras["activity_journal"] = journal.to_list()
        ledger.checkpoint_result(task.id, result)

        # Verify cursor is at step_index == crash_at - 1
        cursor = load_cursor(result)
        assert cursor is not None
        assert cursor.step_index == crash_at - 1
        assert cursor.step_id == _STEPS[crash_at - 1][0]
        assert cursor.activity_key == _STEPS[crash_at - 1][1]
        assert len(journal) == crash_at

        # Pass 2: simulate process restart — fresh ledger instance, same file
        ledger_b = TaskLedger(path=ledger.path, progress_file=str(ledger.progress_path))
        stored_task = ledger_b.get(task.id)
        assert stored_task.result is not None
        stored_result = stored_task.result
        stored_journal = ActivityJournal.from_list(stored_result.extras.get("activity_journal", []))

        executed_during_resume = _resume_from_cursor(stored_result, stored_journal, task_id=task.id)

        # The resumed run must execute ONLY the steps after the crash.
        expected_resumed_steps = [sid for sid, _ in _STEPS[crash_at:]]
        assert executed_during_resume == expected_resumed_steps

        # Final cursor should be at the last step (step_index == 3).
        final_cursor = load_cursor(stored_result)
        assert final_cursor is not None
        assert final_cursor.step_index == len(_STEPS) - 1
        assert final_cursor.step_id == _STEPS[-1][0]

    def test_no_duplicate_activity_execution_across_restart(self, ledger: TaskLedger) -> None:
        """Core acceptance: resume must NOT re-execute completed activities.

        Confirmed by checking the activity_id of the replayed journal:
        steps that were completed before the crash retain their original
        act_NNN id; steps executed on resume get the _resumed suffix.
        """
        task = _seed_claimed_task(ledger)
        result = TaskResult(raw_output="")
        journal = ActivityJournal()

        with pytest.raises(RuntimeError):
            _execute_until_crash(result, journal, task_id=task.id, crash_at_step=2)

        # Two steps should be cached
        original_ids = {rec.idempotency_key: rec.activity_id for rec in journal.records}
        assert len(original_ids) == 2

        # Resume
        result.extras["activity_journal"] = journal.to_list()
        resumed_journal = ActivityJournal.from_list(result.extras["activity_journal"])
        _resume_from_cursor(result, resumed_journal, task_id=task.id)

        # The two steps executed in pass 1 keep their original activity_ids;
        # new steps get _resumed ids.
        for rec in resumed_journal.records:
            if rec.idempotency_key in original_ids:
                assert rec.activity_id == original_ids[rec.idempotency_key], (
                    f"Cached step {rec.idempotency_key} was re-executed"
                )

    def test_resume_without_cursor_starts_from_step_zero(self, ledger: TaskLedger) -> None:
        """No cursor (fresh task or pre-v0.2) resumes from step 0."""
        task = _seed_claimed_task(ledger)
        result = TaskResult(raw_output="")
        journal = ActivityJournal()

        executed = _resume_from_cursor(result, journal, task_id=task.id)
        assert executed == [sid for sid, _ in _STEPS]


class TestCursorReplayMetadata:
    """WCP-011 success criterion: `Replay metadata clearly shows cursor
    progression`."""

    def test_cursor_progression_is_visible_in_trace_steps(self, ledger: TaskLedger) -> None:
        task = _seed_claimed_task(ledger)
        result = TaskResult(raw_output="")
        journal = ActivityJournal()

        with pytest.raises(RuntimeError):
            _execute_until_crash(result, journal, task_id=task.id, crash_at_step=3)

        cursor = load_cursor(result)
        assert cursor is not None

        # The cursor's step_id must match the trace_step at cursor.step_index.
        trace_step = result.trace_steps[cursor.step_index]
        assert trace_step.step_id == cursor.step_id

        # is_step_completed must agree with the cursor position.
        for i in range(cursor.step_index + 1):
            assert is_step_completed(result, _STEPS[i][0])
        for i in range(cursor.step_index + 1, len(result.trace_steps)):
            if i < len(result.trace_steps):
                assert not is_step_completed(result, _STEPS[i][0])

    def test_cursor_carries_activity_key_for_journal_correlation(self, ledger: TaskLedger) -> None:
        task = _seed_claimed_task(ledger)
        result = TaskResult(raw_output="")
        journal = ActivityJournal()

        with pytest.raises(RuntimeError):
            _execute_until_crash(result, journal, task_id=task.id, crash_at_step=2)

        cursor = load_cursor(result)
        assert cursor is not None
        # The cursor's activity_key must be findable in the journal.
        assert journal.get(cursor.activity_key) is not None


class TestCursorCrossTaskIsolation:
    """Cursor task_id mismatches must fail closed — replay must never
    apply a cursor from task A to task B."""

    def test_loading_cursor_from_different_task_is_rejected_by_advance(
        self, ledger: TaskLedger
    ) -> None:
        task_a = _seed_claimed_task(ledger, task_id="task_a")
        result = TaskResult(raw_output="")

        # Stamp a cursor belonging to task_a
        advance_cursor(result=result, task_id=task_a.id, step_id="step_a")

        # Attempting to advance as a different task raises.
        from veridian.loop.checkpoint_cursor import CheckpointCursorError

        with pytest.raises(CheckpointCursorError, match="task_id mismatch"):
            advance_cursor(result=result, task_id="task_b", step_id="step_b")
