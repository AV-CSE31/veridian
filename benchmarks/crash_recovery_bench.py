#!/usr/bin/env python3
"""
Crash-recovery benchmark: SIGKILL a ledger writer mid-stream, then measure loss.

For each run a worker subprocess performs continuous task state transitions
(add -> claim -> submit_result -> mark_done) against a TaskLedger, appending
one acknowledgement line to a sidecar log *after* each ledger call returns.
The parent kills the worker with SIGKILL at a random point, reopens the
ledger, and checks every acknowledged operation is still visible:

* acknowledged-but-missing operation  -> lost_ops (durability violation)
* ledger fails to load                -> corrupted_runs (atomicity violation)
* leftover ledger_*.tmp files         -> orphan_tmp_files (cleanup debt)
* IN_PROGRESS tasks after recovery    -> recovered_in_progress (expected; the
  crash-recovery contract resets them to PENDING via reset_in_progress())

Scope note: SIGKILL validates atomic-rename semantics and ack ordering. It
cannot simulate power loss --- the kernel page cache survives process death,
so fsync gaps are invisible here. Power-loss durability is covered by the
fsync in TaskLedger._write_raw and requires a VM/power-cut rig to test
end to end.

Usage:
    python benchmarks/crash_recovery_bench.py --runs 10
    python benchmarks/crash_recovery_bench.py --runs 50 --min-kill-ms 5 --max-kill-ms 300
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger

# Minimum ledger state implied by each acknowledged operation.
_MIN_STATE: dict[str, set[TaskStatus]] = {
    "add": set(TaskStatus),
    "claim": set(TaskStatus) - {TaskStatus.PENDING},
    "submit": {TaskStatus.VERIFYING, TaskStatus.DONE},
    "done": {TaskStatus.DONE},
}


def _worker(ledger_path: str, ack_path: str) -> None:
    """Write transitions forever; the parent SIGKILLs us at a random point."""
    ledger = TaskLedger(
        path=Path(ledger_path), progress_file=str(Path(ledger_path).parent / "progress.md")
    )
    i = 0
    with open(ack_path, "a", encoding="utf-8") as ack:

        def record(op: str, task_id: str) -> None:
            ack.write(f"{op} {task_id}\n")
            ack.flush()

        while True:
            task = Task(title=f"bench-{i}", description="crash-recovery benchmark task")
            ledger.add([task])
            record("add", task.id)
            ledger.claim(task.id, runner_id="bench-worker")
            record("claim", task.id)
            ledger.submit_result(task.id, TaskResult(raw_output="ok"))
            record("submit", task.id)
            ledger.mark_done(task.id, TaskResult(raw_output="ok", verified=True))
            record("done", task.id)
            i += 1


def _check_run(ledger_path: Path, ack_path: Path) -> dict[str, Any]:
    """Reopen the ledger post-kill and reconcile against acknowledged ops."""
    outcome: dict[str, Any] = {
        "acked_ops": 0,
        "lost_ops": 0,
        "loss_detail": [],
        "corrupted": False,
        "recovered_in_progress": 0,
        "orphan_tmp_files": 0,
    }

    acked: dict[str, str] = {}  # task_id -> furthest acked op
    if ack_path.exists():
        for line in ack_path.read_text(encoding="utf-8").splitlines():
            op, _, task_id = line.partition(" ")
            if op in _MIN_STATE and task_id:
                acked[task_id] = op
                outcome["acked_ops"] += 1

    try:
        ledger = TaskLedger(
            path=ledger_path, progress_file=str(ledger_path.parent / "progress-check.md")
        )
        for task_id, op in acked.items():
            try:
                status = ledger.get(task_id).status
            except Exception:
                outcome["lost_ops"] += 1
                outcome["loss_detail"].append({"task": task_id, "acked": op, "found": None})
                continue
            if status not in _MIN_STATE[op]:
                outcome["lost_ops"] += 1
                outcome["loss_detail"].append({"task": task_id, "acked": op, "found": status.value})
        outcome["recovered_in_progress"] = ledger.reset_in_progress()
    except Exception as exc:
        outcome["corrupted"] = True
        outcome["corruption_error"] = f"{type(exc).__name__}: {exc}"

    outcome["orphan_tmp_files"] = len(list(ledger_path.parent.glob("ledger_*.tmp")))
    return outcome


def _run_once(min_kill_ms: int, max_kill_ms: int, rng: random.Random) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="veridian-crash-bench-") as td:
        ledger_path = Path(td) / "ledger.json"
        ack_path = Path(td) / "acks.log"
        cmd = [
            sys.executable,
            __file__,
            "--worker",
            "--ledger",
            str(ledger_path),
            "--ack",
            str(ack_path),
        ]
        child = subprocess.Popen(cmd)  # noqa: S603
        # Start the kill window from the FIRST acknowledged op, not from
        # process spawn: interpreter + import startup dwarfs small windows
        # and would otherwise kill the worker before it does any work.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if ack_path.exists() and ack_path.stat().st_size > 0:
                break
            time.sleep(0.005)
        else:
            child.kill()
            child.wait()
            raise RuntimeError("worker produced no acknowledged ops within 30s")
        kill_after = rng.uniform(min_kill_ms, max_kill_ms) / 1000.0
        time.sleep(kill_after)
        child.kill()  # SIGKILL: no cleanup handlers run
        child.wait()
        outcome = _check_run(ledger_path, ack_path)
        outcome["kill_after_ms"] = round(kill_after * 1000, 1)
        return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--min-kill-ms", type=int, default=10)
    parser.add_argument("--max-kill-ms", type=int, default=250)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ledger", help=argparse.SUPPRESS)
    parser.add_argument("--ack", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        _worker(args.ledger, args.ack)
        return 0  # pragma: no cover --- worker loops until killed

    rng = random.Random(args.seed)
    runs = [_run_once(args.min_kill_ms, args.max_kill_ms, rng) for _ in range(args.runs)]
    report = {
        "benchmark": "crash_recovery",
        "runs": args.runs,
        "acked_ops": sum(r["acked_ops"] for r in runs),
        "lost_ops": sum(r["lost_ops"] for r in runs),
        "corrupted_runs": sum(1 for r in runs if r["corrupted"]),
        "recovered_in_progress": sum(r["recovered_in_progress"] for r in runs),
        "orphan_tmp_files": sum(r["orphan_tmp_files"] for r in runs),
        "loss_detail": [d for r in runs for d in r["loss_detail"]],
        "passed": all(not r["corrupted"] and r["lost_ops"] == 0 for r in runs),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
