"""
ADVERSARIAL AUDIT — Iteration 4: Crash-safety.

README claims: "crash-safe ledger writes" and "retryable failures instead of
silent success". These tests assert recovery properties and fail against the
shipped recovery path.

  I4-1 (P1): a crash DURING verification strands the task in VERIFYING forever.
             reset_in_progress() only resets IN_PROGRESS, so the task is never
             retried, never failed, never picked up again — silent permanent
             stall. Verification is the likeliest crash point (it runs
             bash/http/LLM), so this is the common case, not the corner case.
  I4-2 (P1): ledger _write_raw never fsyncs before os.replace. The project's own
             atomic_io.py documents this exact gap as a durability bug ("a crash
             between flush and replace can leave the file empty"). The
             safety-critical file does not honor the project's own contract.
  I4-3 (P2): the post-power-loss artifact (a 0-byte ledger) permanently bricks
             the ledger — reset_in_progress(), the documented crash-recovery
             entrypoint, itself raises LedgerCorrupted instead of self-healing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import veridian.ledger.ledger as ledger_mod
from veridian.core.exceptions import LedgerCorrupted
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger


def _ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(tmp_path / "ledger.json", progress_file=str(tmp_path / "progress.md"))


def test_I4_1_crash_during_verification_is_recoverable(tmp_path: Path) -> None:
    """Simulate the runner crashing AFTER submit_result (status=VERIFYING) and
    BEFORE mark_done. A new runner starts and runs crash recovery. The task must
    be re-runnable. It is not: it is stranded in VERIFYING forever.
    """
    led = _ledger(tmp_path)
    task = Task(title="verify-me", verifier_id="schema")
    led.add([task])
    led.claim(task.id, "runner-A")
    led.submit_result(task.id, TaskResult(raw_output="x", structured={"ok": 1}))
    assert led.get(task.id).status == TaskStatus.VERIFYING

    # --- process crash here (mark_done never runs) ---

    # New runner, same ledger file. Standard startup recovery.
    restarted = _ledger(tmp_path)
    restarted.reset_in_progress()

    recovered = restarted.get(task.id)
    next_task = restarted.get_next()
    assert recovered.status == TaskStatus.PENDING or next_task is not None, (
        f"Task stranded in {recovered.status.value!r} after crash-during-verification. "
        "reset_in_progress only recovers IN_PROGRESS; a crash while the verifier "
        "was running (bash/http/LLM — the slowest, likeliest crash window) leaves "
        "the task permanently un-runnable. 'Crash-safe' and 'retryable failures "
        "instead of silent success' are both false here."
    )


def test_I4_2_ledger_write_fsyncs_before_rename(tmp_path: Path, monkeypatch) -> None:
    """The durability contract the project documents in atomic_io.py: fsync the
    temp file before renaming, or a power-loss crash can publish an empty file.
    The ledger's private _write_raw must honor it.
    """
    fsync_calls: list[int] = []
    real_fsync = ledger_mod.os.fsync
    monkeypatch.setattr(ledger_mod.os, "fsync", lambda fd: fsync_calls.append(fd) or real_fsync(fd))

    led = _ledger(tmp_path)
    led.add([Task(title="durable?", verifier_id="schema")])

    assert fsync_calls, (
        "Ledger write never fsyncs before os.replace. The project's own "
        "atomic_io.py exists to fix exactly this ('without this a crash between "
        "flush and replace can leave the file empty'), but the ledger — the most "
        "safety-critical file — reimplements the write and skips the fix."
    )


def test_I4_3_zero_byte_ledger_self_heals(tmp_path: Path) -> None:
    """A 0-byte ledger.json is the artifact a power-loss-between-flush-and-rename
    leaves behind. The documented crash-recovery entrypoint, reset_in_progress(),
    must recover from it — not raise. It raises LedgerCorrupted, so 'crash
    recovery' crashes on the very input it exists to handle.
    """
    led = _ledger(tmp_path)
    led.add([Task(title="t", verifier_id="schema")])
    led.path.write_text("")  # power-loss artifact

    try:
        led.reset_in_progress()
    except LedgerCorrupted:
        pytest.fail(
            "reset_in_progress() raised LedgerCorrupted on a 0-byte ledger. The "
            "crash-recovery routine cannot recover from the canonical crash "
            "artifact; the operator must hand-edit JSON to un-brick the runner."
        )
