"""Default checksummed-WAL behavior and snapshot-only compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger


@pytest.fixture
def wal_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskLedger:
    monkeypatch.delenv("VERIDIAN_LEDGER_WAL", raising=False)
    return TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p.md"))


def _lifecycle(ledger: TaskLedger) -> tuple[str, str]:
    done = Task(title="done-task", description="d")
    failed = Task(title="failed-task", description="d")
    ledger.add([done, failed])
    ledger.claim(done.id, runner_id="w")
    ledger.submit_result(done.id, TaskResult(raw_output="ok"))
    ledger.mark_done(done.id, TaskResult(raw_output="ok"))
    ledger.claim(failed.id, runner_id="w")
    ledger.mark_failed(failed.id, "boom")
    return done.id, failed.id


class TestWalParity:
    def test_lifecycle_state_matches_snapshot_only_opt_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERIDIAN_LEDGER_WAL", "0")
        plain = TaskLedger(path=tmp_path / "plain.json", progress_file=str(tmp_path / "p1.md"))
        plain_done, plain_failed = _lifecycle(plain)

        monkeypatch.delenv("VERIDIAN_LEDGER_WAL")
        wal = TaskLedger(path=tmp_path / "wal.json", progress_file=str(tmp_path / "p2.md"))
        wal_done, wal_failed = _lifecycle(wal)

        assert plain.get(plain_done).status == wal.get(wal_done).status == TaskStatus.DONE
        assert plain.get(plain_failed).status == wal.get(wal_failed).status == TaskStatus.FAILED
        assert plain.stats().by_status == wal.stats().by_status

    def test_cold_reopen_replays_wal(self, wal_ledger: TaskLedger, tmp_path: Path) -> None:
        done_id, failed_id = _lifecycle(wal_ledger)
        reopened = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p2.md"))
        assert reopened.get(done_id).status == TaskStatus.DONE
        assert reopened.get(failed_id).status == TaskStatus.FAILED

    def test_transitions_append_instead_of_rewriting_snapshot(
        self, wal_ledger: TaskLedger, tmp_path: Path
    ) -> None:
        done_id, _ = _lifecycle(wal_ledger)
        snapshot = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
        assert snapshot["tasks"] == {}  # all state lives in the WAL pre-compaction
        records = [
            json.loads(line)
            for line in (tmp_path / "ledger.json.wal").read_text(encoding="utf-8").splitlines()
        ]
        assert records
        assert all(record["checksum"].startswith("sha256:") for record in records)
        assert (tmp_path / "ledger.json.wal.head").is_file()
        assert wal_ledger.get(done_id).status == TaskStatus.DONE

    def test_v2_snapshot_without_wal_opens_transparently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERIDIAN_LEDGER_WAL", "0")
        plain = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p.md"))
        t = Task(title="legacy", description="d")
        plain.add([t])
        assert not (tmp_path / "ledger.json.wal").exists()

        monkeypatch.delenv("VERIDIAN_LEDGER_WAL")
        wal = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p.md"))
        assert wal.get(t.id).title == "legacy"
        wal.claim(t.id, runner_id="w")  # appends rather than rewriting
        assert wal.get(t.id).status == TaskStatus.IN_PROGRESS


class TestWalCrashSemantics:
    def test_torn_tail_is_ignored_by_readers(self, wal_ledger: TaskLedger, tmp_path: Path) -> None:
        done_id, _ = _lifecycle(wal_ledger)
        with open(tmp_path / "ledger.json.wal", "a", encoding="utf-8") as f:
            f.write('{"seq": 99, "tasks": [{"id": "ghost"')  # torn: no newline
        reopened = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p2.md"))
        assert reopened.get(done_id).status == TaskStatus.DONE

    def test_writer_repairs_torn_tail_before_appending(
        self, wal_ledger: TaskLedger, tmp_path: Path
    ) -> None:
        done_id, failed_id = _lifecycle(wal_ledger)
        wal_path = tmp_path / "ledger.json.wal"
        with open(wal_path, "a", encoding="utf-8") as f:
            f.write("GARBAGE-NOT-JSON")

        writer = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p2.md"))
        writer.reset_failed([failed_id])

        # The torn fragment must be gone and every line must parse: a valid
        # line appended after a torn fragment would be unreachable on replay.
        for line in wal_path.read_text(encoding="utf-8").splitlines():
            json.loads(line)
        assert writer.get(failed_id).status == TaskStatus.PENDING
        assert writer.get(done_id).status == TaskStatus.DONE

    def test_crash_between_snapshot_and_truncate_is_idempotent(
        self, wal_ledger: TaskLedger, tmp_path: Path
    ) -> None:
        done_id, failed_id = _lifecycle(wal_ledger)
        before = {t.id: t.status for t in wal_ledger.list()}
        # Simulate the compaction crash window: snapshot written, WAL kept.
        wal_ledger._write_raw(wal_ledger._read_raw())

        reopened = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p2.md"))
        assert {t.id: t.status for t in reopened.list()} == before
        assert reopened.get(done_id).status == TaskStatus.DONE
        assert reopened.get(failed_id).status == TaskStatus.FAILED


class TestWalCompaction:
    def test_compaction_snapshots_and_truncates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERIDIAN_LEDGER_WAL", raising=False)
        monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "4")
        ledger = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p.md"))
        # 6 commits: compaction fires at entry 4, then 2 more entries append.
        done_id, failed_id = _lifecycle(ledger)

        snapshot = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
        assert len(snapshot["tasks"]) == 2  # compaction moved state into the snapshot
        wal_records = [
            json.loads(line)
            for line in (tmp_path / "ledger.json.wal").read_text(encoding="utf-8").splitlines()
        ]
        assert len(wal_records) == 2  # only post-compaction entries remain
        assert [record["seq"] for record in wal_records] == [1, 2]
        assert wal_records[1]["previous_hash"] == wal_records[0]["checksum"]
        assert snapshot["_ledger"]["generation"] == wal_records[0]["generation"] == 2
        assert len(list(tmp_path.glob("ledger.json.wal.g*.sealed"))) == 1

        reopened = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "p2.md"))
        assert reopened.get(done_id).status == TaskStatus.DONE
        assert reopened.get(failed_id).status == TaskStatus.FAILED


class TestWalIsolation:
    def test_returned_task_does_not_contaminate_write_cache(self, wal_ledger: TaskLedger) -> None:
        t = Task(title="iso", description="d", metadata={"k": "v"})
        wal_ledger.add([t])
        claimed = wal_ledger.claim(t.id, runner_id="w")
        claimed.metadata["injected"] = True

        assert "injected" not in wal_ledger.get(t.id).metadata
        wal_ledger.submit_result(t.id, TaskResult(raw_output="ok"))
        assert "injected" not in wal_ledger.get(t.id).metadata

    def test_cross_instance_visibility(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERIDIAN_LEDGER_WAL", raising=False)
        a = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "pa.md"))
        b = TaskLedger(path=tmp_path / "ledger.json", progress_file=str(tmp_path / "pb.md"))

        t = Task(title="shared", description="d")
        a.add([t])
        assert b.get(t.id).title == "shared"
        b.claim(t.id, runner_id="b")
        # a's writer cache must detect b's append via the stamp and reload.
        assert a.get(t.id).status == TaskStatus.IN_PROGRESS
        a.mark_failed(t.id, "from-a")
        assert b.get(t.id).status == TaskStatus.FAILED
