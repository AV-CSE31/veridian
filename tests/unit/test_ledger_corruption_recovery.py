"""
tests.unit.test_ledger_corruption_recovery
------------------------------------------------------------------------------------------------------------------------------
Durability tests for ``TaskLedger`` under failure modes:

- malformed JSON --- ``LedgerCorrupted``
- non-object root --- ``LedgerCorrupted``
- empty ledger file --- recover from WAL evidence or fail closed
- orphaned ``.tmp`` files left by a crashed writer don't break subsequent reads
- ``reset_in_progress`` is idempotent (safe to call repeatedly after partial
  recovery)
- FileLock contention surfaces deterministically (does not silently corrupt
  state when a competing holder is active)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from veridian.core.task import Task, TaskStatus
from veridian.ledger.ledger import TaskLedger


def _mk_ledger(tmp_path: Path, **kwargs: object) -> TaskLedger:
    return TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
        **kwargs,  # type: ignore[arg-type]
    )


def _task(title: str = "test") -> Task:
    return Task(title=title, description="do the thing")


class TestMalformedJsonDetection:
    def test_truncated_snapshot_recovers_from_valid_wal(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        task = _task()
        ledger.add([task])

        # Simulate a crash that left a truncated file behind.
        ledger.path.write_text('{"schema_version": 1, "tasks": {"foo"', encoding="utf-8")

        assert ledger.get(task.id).title == task.title

    def test_non_object_snapshot_recovers_from_valid_wal(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        task = _task()
        ledger.add([task])

        ledger.path.write_text("[]", encoding="utf-8")

        assert ledger.get(task.id).title == task.title

    def test_empty_file_recovers_from_wal_without_losing_tasks(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        task = _task()
        ledger.add([task])

        ledger.path.write_text("", encoding="utf-8")

        assert [item.id for item in ledger.list()] == [task.id]
        restarted = _mk_ledger(tmp_path)
        assert restarted.get(task.id).title == task.title
        assert list(tmp_path.glob("ledger.json.empty.*.corrupt"))

    def test_missing_file_is_treated_as_empty_not_corrupted(self, tmp_path: Path) -> None:
        # Fresh ledger with no file on disk yet --- list() must succeed empty,
        # not raise. This guarantees first-run UX.
        ledger = _mk_ledger(tmp_path)
        assert ledger.list() == []


class TestCrashedWriterArtifacts:
    def test_orphan_tmp_file_does_not_break_reads(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        ledger.add([_task()])

        # Simulate a crash mid-write: the atomic-rename path leaves a
        # ``ledger_*.tmp`` orphan if the process dies between mkstemp and
        # os.replace.
        orphan = tmp_path / "ledger_orphan.tmp"
        orphan.write_text('{"partial": true}', encoding="utf-8")

        # Read path must not be confused by the orphan.
        assert len(ledger.list()) == 1
        assert orphan.exists(), "orphan must persist until manual gc"

    def test_orphan_tmp_file_does_not_block_writes(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        ledger.add([_task("first")])

        orphan = tmp_path / "ledger_orphan.tmp"
        orphan.write_text('{"partial": true}', encoding="utf-8")

        # A subsequent write should succeed regardless of the orphan.
        ledger.add([_task("second")])
        assert len(ledger.list()) == 2


class TestResetInProgressIdempotence:
    def test_double_reset_is_safe(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        t = _task()
        ledger.add([t])
        ledger.claim(t.id, "crashed-runner")

        first = ledger.reset_in_progress()
        second = ledger.reset_in_progress()

        assert first == 1
        # Second call is a no-op --- task is already PENDING.
        assert second == 0
        assert ledger.get(t.id).status == TaskStatus.PENDING
        assert ledger.get(t.id).claimed_by is None

    def test_reset_preserves_done_and_failed(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        done_t = _task("done")
        failed_t = _task("failed")
        in_progress_t = _task("in-progress")
        ledger.add([done_t, failed_t, in_progress_t])

        from veridian.core.task import TaskResult

        # Done task
        ledger.claim(done_t.id, "r")
        ledger.submit_result(done_t.id, TaskResult(raw_output="ok"))
        ledger.mark_done(done_t.id, TaskResult(raw_output="ok"))

        # Failed task
        ledger.claim(failed_t.id, "r")
        ledger.mark_failed(failed_t.id, "boom")

        # Stale in-progress
        ledger.claim(in_progress_t.id, "r")

        reset = ledger.reset_in_progress()
        assert reset == 1
        assert ledger.get(done_t.id).status == TaskStatus.DONE
        assert ledger.get(failed_t.id).status == TaskStatus.FAILED
        assert ledger.get(in_progress_t.id).status == TaskStatus.PENDING


class TestFileLockContention:
    def test_competing_lock_holder_causes_timeout(self, tmp_path: Path) -> None:
        # Two ledger instances point at the same file. One holds the lock;
        # the other must time out rather than silently corrupt.
        ledger = _mk_ledger(tmp_path, lock_timeout=0.2)
        ledger.add([_task()])  # initial state

        external_lock = FileLock(str(ledger._lock_path))
        with external_lock, pytest.raises(Timeout):
            ledger.add([_task("second")])

        # After the external holder releases, writes resume normally.
        ledger.add([_task("third")])
        assert len(ledger.list()) == 2

    def test_corrupted_snapshot_after_write_recovers_exact_state(self, tmp_path: Path) -> None:
        ledger = _mk_ledger(tmp_path)
        task = _task()
        ledger.add([task])

        good_text = ledger.path.read_text(encoding="utf-8")
        assert json.loads(good_text)  # sanity

        ledger.path.write_text(good_text[:30], encoding="utf-8")

        assert ledger.get(task.id).title == task.title
