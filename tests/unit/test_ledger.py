"""
tests.unit.test_ledger
───────────────────────
Unit tests for TaskLedger — state machine, atomic writes, crash recovery.
All tests use tmp_path (pytest fixture) so no disk residue.
"""

from pathlib import Path

import pytest

from veridian.core.exceptions import InvalidTransition, TaskAlreadyClaimed, TaskNotFound
from veridian.core.task import Task, TaskPriority, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "progress.md"))


def make_task(**kwargs) -> Task:
    defaults = dict(title="test task", description="do the thing")
    defaults.update(kwargs)
    return Task(**defaults)


# ── Basic CRUD ────────────────────────────────────────────────────────────────


class TestLedgerBasic:
    def test_add_and_get(self, ledger):
        t = make_task(title="hello")
        count = ledger.add([t])
        assert count == 1
        fetched = ledger.get(t.id)
        assert fetched.title == "hello"

    def test_get_missing_raises(self, ledger):
        with pytest.raises(TaskNotFound):
            ledger.get("nonexistent-id")

    def test_add_skip_duplicates(self, ledger):
        t = make_task()
        ledger.add([t])
        count = ledger.add([t], skip_duplicates=True)
        assert count == 0
        assert len(ledger.list()) == 1

    def test_add_override_duplicates(self, ledger):
        t = make_task(title="original")
        ledger.add([t])
        t2 = Task(id=t.id, title="updated")
        count = ledger.add([t2], skip_duplicates=False)
        assert count == 1
        assert ledger.get(t.id).title == "updated"

    def test_list_all(self, ledger):
        tasks = [make_task(title=f"task {i}") for i in range(5)]
        ledger.add(tasks)
        result = ledger.list()
        assert len(result) == 5

    def test_list_by_status(self, ledger):
        t1 = make_task(title="pending one")
        t2 = make_task(title="pending two")
        ledger.add([t1, t2])
        ledger.claim(t1.id, "runner-1")
        result = ledger.list(status=TaskStatus.IN_PROGRESS)
        assert len(result) == 1
        assert result[0].id == t1.id

    def test_list_by_phase(self, ledger):
        t1 = make_task(phase="phase_a")
        t2 = make_task(phase="phase_b")
        ledger.add([t1, t2])
        result = ledger.list(phase="phase_a")
        assert len(result) == 1


# ── State machine ─────────────────────────────────────────────────────────────


class TestLedgerStateMachine:
    def test_claim_transitions_to_in_progress(self, ledger):
        t = make_task()
        ledger.add([t])
        updated = ledger.claim(t.id, "runner-1")
        assert updated.status == TaskStatus.IN_PROGRESS
        assert updated.claimed_by == "runner-1"

    def test_double_claim_same_runner_is_ok(self, ledger):
        t = make_task()
        ledger.add([t])
        ledger.claim(t.id, "runner-1")
        ledger.claim(t.id, "runner-1")  # idempotent for same runner

    def test_double_claim_different_runner_raises(self, ledger):
        t = make_task()
        ledger.add([t])
        ledger.claim(t.id, "runner-1")
        with pytest.raises(TaskAlreadyClaimed):
            ledger.claim(t.id, "runner-2")

    def test_mark_done(self, ledger):
        t = make_task()
        ledger.add([t])
        ledger.claim(t.id, "runner-1")
        ledger.submit_result(t.id, TaskResult(raw_output="done"))
        result = TaskResult(raw_output="done", structured={"answer": "42"})
        updated = ledger.mark_done(t.id, result)
        assert updated.status == TaskStatus.DONE
        assert updated.result.verified is True
        assert updated.result.verified_at is not None

    def test_mark_failed_increments_retry(self, ledger):
        t = make_task(max_retries=2)
        ledger.add([t])
        ledger.claim(t.id, "runner-1")
        updated = ledger.mark_failed(t.id, "test error")
        assert updated.status == TaskStatus.FAILED
        assert updated.retry_count == 1
        assert updated.last_error == "test error"

    def test_mark_failed_abandons_after_max_retries(self, ledger):
        t = make_task(max_retries=1)
        ledger.add([t])

        # First failure → FAILED
        ledger.claim(t.id, "runner-1")
        updated = ledger.mark_failed(t.id, "error 1")
        assert updated.status == TaskStatus.FAILED
        assert updated.retry_count == 1

        # Reset to PENDING (simulate re-queue)
        ledger.reset_failed([t.id])

        # Second failure → ABANDONED (exceeds max_retries=1)
        ledger.claim(t.id, "runner-1")
        updated = ledger.mark_failed(t.id, "error 2")
        assert updated.status == TaskStatus.ABANDONED
        assert updated.retry_count == 2

    def test_invalid_transition_raises(self, ledger):
        t = make_task()
        ledger.add([t])
        # Cannot go from PENDING → DONE directly
        with pytest.raises(InvalidTransition):
            ledger._transition(t, TaskStatus.DONE)

    def test_skip(self, ledger):
        t = make_task()
        ledger.add([t])
        updated = ledger.skip(t.id, reason="out of scope")
        assert updated.status == TaskStatus.SKIPPED
        assert updated.last_error == "out of scope"


# ── get_next priority + dependency ordering ───────────────────────────────────


class TestGetNext:
    def test_returns_highest_priority_first(self, ledger):
        low = make_task(title="low", priority=TaskPriority.LOW)
        high = make_task(title="high", priority=TaskPriority.HIGH)
        ledger.add([low, high])
        nxt = ledger.get_next()
        assert nxt.title == "high"

    def test_returns_none_when_empty(self, ledger):
        assert ledger.get_next() is None

    def test_returns_none_when_all_done(self, ledger):
        t = make_task()
        ledger.add([t])
        ledger.claim(t.id, "r")
        ledger.submit_result(t.id, TaskResult(raw_output="ok"))
        ledger.mark_done(t.id, TaskResult(raw_output="ok"))
        assert ledger.get_next() is None

    def test_respects_phase_filter(self, ledger):
        a = make_task(title="phase_a task", phase="phase_a")
        b = make_task(title="phase_b task", phase="phase_b")
        ledger.add([a, b])
        nxt = ledger.get_next(phase="phase_b")
        assert nxt.title == "phase_b task"

    def test_blocks_on_unresolved_dependency(self, ledger):
        dep = make_task(title="dep")
        child = make_task(title="child", depends_on=[dep.id])
        ledger.add([dep, child])
        # dep is PENDING, so child should be blocked
        nxt = ledger.get_next()
        assert nxt.id == dep.id  # dep comes first

    def test_unblocks_when_dependency_done(self, ledger):
        dep = make_task(title="dep", priority=10)
        child = make_task(title="child", depends_on=[dep.id], priority=100)
        ledger.add([dep, child])

        # Complete dep
        ledger.claim(dep.id, "r")
        ledger.submit_result(dep.id, TaskResult(raw_output="ok"))
        ledger.mark_done(dep.id, TaskResult(raw_output="ok"))

        # Now child (higher priority) should be returned
        nxt = ledger.get_next()
        assert nxt.id == child.id


# ── Crash recovery ────────────────────────────────────────────────────────────


class TestCrashRecovery:
    def test_reset_in_progress_clears_stale_claims(self, ledger):
        t = make_task()
        ledger.add([t])
        ledger.claim(t.id, "crashed-runner")

        # Simulate crash recovery on new run
        count = ledger.reset_in_progress()
        assert count == 1
        assert ledger.get(t.id).status == TaskStatus.PENDING
        assert ledger.get(t.id).claimed_by is None

    def test_reset_in_progress_by_runner_id(self, ledger):
        t1 = make_task(title="runner-A task")
        t2 = make_task(title="runner-B task")
        ledger.add([t1, t2])
        ledger.claim(t1.id, "runner-A")
        ledger.claim(t2.id, "runner-B")

        # Only reset runner-A's tasks
        count = ledger.reset_in_progress(runner_id="runner-A")
        assert count == 1
        assert ledger.get(t1.id).status == TaskStatus.PENDING
        assert ledger.get(t2.id).status == TaskStatus.IN_PROGRESS

    def test_reset_failed_re_queues(self, ledger):
        t = make_task(max_retries=3)
        ledger.add([t])
        ledger.claim(t.id, "r")
        ledger.mark_failed(t.id, "transient error")
        assert ledger.get(t.id).status == TaskStatus.FAILED

        count = ledger.reset_failed([t.id])
        assert count == 1
        restored = ledger.get(t.id)
        assert restored.status == TaskStatus.PENDING
        # retry_count is preserved across resets so abandonment threshold is accurate
        assert restored.retry_count == 1


# ── Stats ─────────────────────────────────────────────────────────────────────


class TestStats:
    def test_stats_counts(self, ledger):
        tasks = [make_task() for _ in range(5)]
        ledger.add(tasks)
        ledger.claim(tasks[0].id, "r")
        ledger.submit_result(tasks[0].id, TaskResult(raw_output="ok"))
        ledger.mark_done(tasks[0].id, TaskResult(raw_output="ok"))

        stats = ledger.stats()
        assert stats.total == 5
        assert stats.done == 1
        assert stats.pending == 4


# ── Progress log ──────────────────────────────────────────────────────────────


class TestProgressLog:
    def test_log_creates_progress_file(self, ledger, tmp_path):
        ledger.log("Run started")
        assert (tmp_path / "progress.md").exists()

    def test_read_recent_log(self, ledger):
        for i in range(15):
            ledger.log(f"event {i}")
        recent = ledger.read_recent_log(n=5)
        assert len(recent) == 5
        assert "event 14" in recent[-1]

    def test_read_recent_log_missing_file(self, ledger):
        # Should return empty list, not raise
        result = ledger.read_recent_log()
        assert result == []
