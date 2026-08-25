"""Public durability guarantees for the local task ledger."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from veridian.core.exceptions import LedgerCorrupted, TaskNotFound
from veridian.core.task import Task
from veridian.ledger import TaskLedger


def test_default_ledger_recovers_acknowledged_task_when_snapshot_disappears(
    tmp_path: Path,
) -> None:
    """A successful mutation remains visible after restart without its snapshot."""
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="settle approved transfer", description="post it exactly once")

    assert ledger.add([task]) == 1
    ledger_path.unlink()

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    assert restarted.get(task.id).title == "settle approved transfer"


def test_zero_byte_snapshot_recovers_only_when_wal_proves_the_state(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="retain authorization", description="do not lose it")
    ledger.add([task])
    ledger_path.write_bytes(b"")

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    assert restarted.get(task.id).title == "retain authorization"
    assert list(tmp_path.glob("ledger.json.empty.*.corrupt"))


def test_zero_byte_snapshot_without_recovery_evidence_fails_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_bytes(b"")

    with pytest.raises(LedgerCorrupted, match="empty"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_complete_wal_rollback_is_detected_on_restart(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    first = Task(title="first acknowledgement")
    second = Task(title="second acknowledgement")
    ledger.add([first])
    ledger.add([second])

    wal_path = tmp_path / "ledger.json.wal"
    records = wal_path.read_bytes().splitlines(keepends=True)
    wal_path.write_bytes(b"".join(records[:-1]))

    with pytest.raises(LedgerCorrupted, match="rollback"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_complete_wal_record_with_bad_checksum_fails_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    ledger.add([Task(title="acknowledged")])
    wal_path = tmp_path / "ledger.json.wal"
    record = wal_path.read_text(encoding="utf-8")
    wal_path.write_text(record.replace('"version":1', '"version":2'), encoding="utf-8")

    with pytest.raises(LedgerCorrupted, match="invalid schema|checksum"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_each_wal_record_contains_only_the_mutated_task(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    first = Task(title="first")
    second = Task(title="second")
    ledger.add([first])
    ledger.add([second])

    records = [json.loads(line) for line in (tmp_path / "ledger.json.wal").read_text().splitlines()]

    assert [[task["id"] for task in record["tasks"]] for record in records] == [
        [first.id],
        [second.id],
    ]


def test_invalid_task_in_snapshot_fails_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": {"bad": {"id": "bad", "status": "not-a-real-status"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LedgerCorrupted, match="task.*invalid"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_snapshot_checksum_detects_complete_tampering(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["updated_at"] = "2000-01-01T00:00:00+00:00"
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerCorrupted, match="snapshot checksum"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_wal_compaction_preserves_state_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "2")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    first = Task(title="first")
    second = Task(title="second")
    ledger.add([first])
    ledger.add([second])

    assert (tmp_path / "ledger.json.wal").read_bytes() == b""
    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert {task.id for task in restarted.list()} == {first.id, second.id}


def test_restart_finishes_compaction_interrupted_after_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from veridian.ledger.wal import WalLog

    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "2")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    first = Task(title="first")
    second = Task(title="second")
    ledger.add([first])
    real_seal = WalLog.seal

    def crash_before_rotation(self: WalLog, *args: object, **kwargs: object) -> Path:
        raise OSError("injected process death before WAL rotation")

    monkeypatch.setattr(WalLog, "seal", crash_before_rotation)
    with pytest.raises(OSError, match="injected process death"):
        ledger.add([second])
    monkeypatch.setattr(WalLog, "seal", real_seal)

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert {task.id for task in restarted.list()} == {first.id, second.id}


def test_valid_ledger_specific_temp_is_promoted_before_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "1")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="must survive rename window")
    ledger.add([task])
    candidate = tmp_path / ".ledger.json.snapshot.recovery.tmp"
    candidate.write_bytes(ledger_path.read_bytes())
    ledger_path.unlink()

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    assert restarted.get(task.id).title == "must survive rename window"
    assert not candidate.exists()


def test_ambiguous_snapshot_temps_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "1")
    snapshots: list[bytes] = []
    for name in ("left", "right"):
        directory = tmp_path / name
        directory.mkdir()
        path = directory / "ledger.json"
        ledger = TaskLedger(path, progress_file=str(directory / "progress.md"))
        ledger.add([Task(title=name)])
        snapshots.append(path.read_bytes())

    target = tmp_path / "target"
    target.mkdir()
    (target / ".ledger.json.snapshot.left.tmp").write_bytes(snapshots[0])
    (target / ".ledger.json.snapshot.right.tmp").write_bytes(snapshots[1])

    with pytest.raises(LedgerCorrupted, match="ambiguous"):
        TaskLedger(target / "ledger.json", progress_file=str(target / "progress.md"))


def test_missing_compacted_snapshot_does_not_bootstrap_over_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "1")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    ledger.add([Task(title="anchored history")])
    ledger_path.unlink()

    with pytest.raises(LedgerCorrupted, match="snapshot is missing"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert not ledger_path.exists()


def test_torn_final_wal_record_is_repaired_before_next_append(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    first = Task(title="before crash")
    ledger.add([first])
    wal_path = tmp_path / "ledger.json.wal"
    with wal_path.open("ab") as handle:
        handle.write(b'{"version":1,"tasks":[')

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    second = Task(title="after restart")
    restarted.add([second])
    final = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    assert {task.id for task in final.list()} == {first.id, second.id}
    assert wal_path.read_bytes().endswith(b"\n")


def test_fsync_failure_prevents_successful_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import veridian.ledger.wal as wal_module

    ledger = TaskLedger(
        tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("durability boundary unavailable")

    monkeypatch.setattr(wal_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="durability boundary unavailable"):
        ledger.add([Task(title="must not be acknowledged")])


def test_record_without_durable_head_is_not_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from veridian.ledger.wal import WalHeadStore

    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="interrupted before acknowledgement")
    real_write = WalHeadStore.write

    def fail_head(self: WalHeadStore, *args: object, **kwargs: object) -> None:
        raise OSError("head publication interrupted")

    monkeypatch.setattr(WalHeadStore, "write", fail_head)
    with pytest.raises(OSError, match="head publication interrupted"):
        ledger.add([task])
    monkeypatch.setattr(WalHeadStore, "write", real_write)

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    with pytest.raises(TaskNotFound, match="not found"):
        restarted.get(task.id)


def test_concurrent_writers_preserve_every_acknowledged_task(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    def add(index: int) -> str:
        task = Task(title=f"concurrent-{index}")
        ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
        assert ledger.add([task]) == 1
        return task.id

    with ThreadPoolExecutor(max_workers=8) as pool:
        acknowledged = set(pool.map(add, range(24)))

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert {task.id for task in restarted.list()} == acknowledged


def test_restart_finishes_compaction_interrupted_after_wal_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from veridian.ledger.wal import WalLog

    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "1")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="sealed before crash")
    real_reset = WalLog.reset

    def crash_before_reset(self: WalLog, *args: object, **kwargs: object) -> None:
        raise OSError("injected process death after WAL seal")

    monkeypatch.setattr(WalLog, "reset", crash_before_reset)
    with pytest.raises(OSError, match="after WAL seal"):
        ledger.add([task])
    monkeypatch.setattr(WalLog, "reset", real_reset)

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert restarted.get(task.id).title == "sealed before crash"


def test_restart_finishes_compaction_interrupted_before_new_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from veridian.ledger.wal import WalHead, WalHeadStore

    monkeypatch.setenv("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES", "1")
    ledger_path = tmp_path / "ledger.json"
    ledger = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    task = Task(title="checkpointed before crash")
    real_write = WalHeadStore.write

    def crash_on_new_generation(self: WalHeadStore, head: WalHead, *, fsync: bool) -> None:
        if head.generation == 2 and head.last_seq == 0:
            raise OSError("injected process death before new WAL head")
        real_write(self, head, fsync=fsync)

    monkeypatch.setattr(WalHeadStore, "write", crash_on_new_generation)
    with pytest.raises(OSError, match="before new WAL head"):
        ledger.add([task])
    monkeypatch.setattr(WalHeadStore, "write", real_write)

    restarted = TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    assert restarted.get(task.id).title == "checkpointed before crash"


def test_snapshot_temps_are_isolated_by_ledger_name(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first = TaskLedger(first_path, progress_file=str(tmp_path / "first.md"))
    second = TaskLedger(second_path, progress_file=str(tmp_path / "second.md"))
    first_task = Task(title="first only")
    second_task = Task(title="second only")
    first.add([first_task])
    second.add([second_task])
    (tmp_path / ".first.json.snapshot.invalid.tmp").write_text("{", encoding="utf-8")

    restarted = TaskLedger(second_path, progress_file=str(tmp_path / "second.md"))

    assert [task.id for task in restarted.list()] == [second_task.id]


def test_checksummed_wal_with_invalid_task_fails_as_ledger_corruption(tmp_path: Path) -> None:
    from veridian.ledger.wal import GENESIS_HASH, WalHead, WalHeadStore, WalLog

    ledger_path = tmp_path / "ledger.json"
    TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    snapshot = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger_id = snapshot["_ledger"]["id"]
    wal = WalLog(tmp_path / "ledger.json.wal")
    wal.reset(fsync=False)
    checksum = wal.append(
        [{"id": "bad", "status": "impossible"}],
        ledger_id=ledger_id,
        generation=1,
        seq=1,
        previous_hash=GENESIS_HASH,
        fsync=False,
    )
    WalHeadStore(tmp_path / "ledger.json.wal.head").write(
        WalHead(ledger_id=ledger_id, generation=1, last_seq=1, last_hash=checksum),
        fsync=False,
    )

    with pytest.raises(LedgerCorrupted, match="task.*invalid"):
        TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))


def test_restart_cleans_only_this_ledgers_abandoned_atomic_temps(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))
    abandoned = tmp_path / ".ledger.json.wal.head.abandoned.tmp"
    unrelated = tmp_path / ".other.json.wal.head.abandoned.tmp"
    abandoned.write_bytes(b"partial")
    unrelated.write_bytes(b"partial")

    TaskLedger(ledger_path, progress_file=str(tmp_path / "progress.md"))

    assert not abandoned.exists()
    assert unrelated.exists()
