#!/usr/bin/env python3
"""Bounded process-kill campaign for the default durable task ledger.

The worker records an acknowledgement only after the corresponding ledger
call returns.  The parent terminates the process at deterministic randomized
points, restarts the ledger, and checks that every acknowledged transition is
still represented.  This exercises process-crash ordering; it is not a
substitute for a VM power-cut campaign.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger import TaskLedger

_MIN_STATE: dict[str, set[TaskStatus]] = {
    "add": set(TaskStatus),
    "claim": set(TaskStatus) - {TaskStatus.PENDING},
    "submit": {TaskStatus.VERIFYING, TaskStatus.DONE},
    "done": {TaskStatus.DONE},
}


def _worker(ledger_path: str, acknowledgement_path: str) -> None:
    ledger_file = Path(ledger_path)
    ledger = TaskLedger(
        ledger_file,
        progress_file=str(ledger_file.parent / "progress.md"),
    )
    index = 0
    with open(acknowledgement_path, "a", encoding="utf-8") as acknowledgements:

        def acknowledge(operation: str, task_id: str) -> None:
            acknowledgements.write(f"{operation} {task_id}\n")
            acknowledgements.flush()
            os.fsync(acknowledgements.fileno())

        while True:
            task = Task(
                title=f"crash-campaign-{index}",
                description="complete every durable transition",
            )
            ledger.add([task])
            acknowledge("add", task.id)
            ledger.claim(task.id, runner_id="crash-campaign")
            acknowledge("claim", task.id)
            result = TaskResult(raw_output="ok")
            ledger.submit_result(task.id, result)
            acknowledge("submit", task.id)
            ledger.mark_done(task.id, result)
            acknowledge("done", task.id)
            index += 1


def _check_run(ledger_path: Path, acknowledgement_path: Path) -> dict[str, Any]:
    acknowledged: dict[str, str] = {}
    acknowledged_operations = 0
    if acknowledgement_path.exists():
        for line in acknowledgement_path.read_text(encoding="utf-8").splitlines():
            operation, separator, task_id = line.partition(" ")
            if separator and operation in _MIN_STATE and task_id:
                acknowledged[task_id] = operation
                acknowledged_operations += 1

    result: dict[str, Any] = {
        "acknowledged_operations": acknowledged_operations,
        "lost_operations": 0,
        "losses": [],
        "corrupted": False,
        "recovered_active_tasks": 0,
        "orphan_temp_files": 0,
    }
    try:
        ledger = TaskLedger(
            ledger_path,
            progress_file=str(ledger_path.parent / "progress-check.md"),
        )
        for task_id, operation in acknowledged.items():
            try:
                status = ledger.get(task_id).status
            except Exception:  # noqa: BLE001 - benchmark records framework failures
                result["lost_operations"] += 1
                result["losses"].append(
                    {"task_id": task_id, "acknowledged": operation, "found": None}
                )
                continue
            if status not in _MIN_STATE[operation]:
                result["lost_operations"] += 1
                result["losses"].append(
                    {
                        "task_id": task_id,
                        "acknowledged": operation,
                        "found": status.value,
                    }
                )
        result["recovered_active_tasks"] = ledger.reset_in_progress()
    except Exception as exc:  # noqa: BLE001 - corruption is benchmark output
        result["corrupted"] = True
        result["corruption_error"] = f"{type(exc).__name__}: {exc}"

    result["orphan_temp_files"] = len(list(ledger_path.parent.glob(".*.tmp")))
    return result


def _run_once(
    minimum_kill_ms: int,
    maximum_kill_ms: int,
    random_source: random.Random,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="veridian-crash-campaign-") as directory:
        ledger_path = Path(directory) / "ledger.json"
        acknowledgement_path = Path(directory) / "acknowledgements.log"
        command = [
            sys.executable,
            __file__,
            "--worker",
            "--ledger",
            str(ledger_path),
            "--acknowledgements",
            str(acknowledgement_path),
        ]
        child = subprocess.Popen(command)  # noqa: S603
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if acknowledgement_path.exists() and acknowledgement_path.stat().st_size:
                break
            time.sleep(0.005)
        else:
            child.kill()
            child.wait()
            raise RuntimeError("worker produced no acknowledgement within 30 seconds")

        kill_after = random_source.uniform(minimum_kill_ms, maximum_kill_ms) / 1000
        time.sleep(kill_after)
        child.kill()
        child.wait()
        result = _check_run(ledger_path, acknowledgement_path)
        result["kill_after_ms"] = round(kill_after * 1000, 1)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--min-kill-ms", type=int, default=5)
    parser.add_argument("--max-kill-ms", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ledger", help=argparse.SUPPRESS)
    parser.add_argument("--acknowledgements", help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    if arguments.worker:
        if arguments.ledger is None or arguments.acknowledgements is None:
            parser.error("worker mode requires ledger and acknowledgement paths")
        _worker(arguments.ledger, arguments.acknowledgements)
        return 0
    if arguments.runs < 1 or arguments.min_kill_ms < 0:
        parser.error("runs must be positive and kill delay must be non-negative")
    if arguments.max_kill_ms < arguments.min_kill_ms:
        parser.error("max-kill-ms must be greater than or equal to min-kill-ms")

    random_source = random.Random(arguments.seed)
    runs = [
        _run_once(arguments.min_kill_ms, arguments.max_kill_ms, random_source)
        for _ in range(arguments.runs)
    ]
    report = {
        "benchmark": "task_ledger_process_kill",
        "backend": "checksummed-wal-v1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "seed": arguments.seed,
        "runs": arguments.runs,
        "acknowledged_operations": sum(item["acknowledged_operations"] for item in runs),
        "lost_operations": sum(item["lost_operations"] for item in runs),
        "corrupted_runs": sum(bool(item["corrupted"]) for item in runs),
        "recovered_active_tasks": sum(item["recovered_active_tasks"] for item in runs),
        "orphan_temp_files": sum(item["orphan_temp_files"] for item in runs),
        "losses": [loss for item in runs for loss in item["losses"]],
    }
    # Keep the pre-WAL benchmark schema available to existing dashboards while
    # the more explicit canonical names above remain authoritative.
    report.update(
        {
            "acked_ops": report["acknowledged_operations"],
            "lost_ops": report["lost_operations"],
            "corruption_runs": report["corrupted_runs"],
            "recovered_in_progress": report["recovered_active_tasks"],
            "orphan_tmp_files": report["orphan_temp_files"],
            "loss_detail": report["losses"],
        }
    )
    report["passed"] = report["lost_operations"] == 0 and report["corrupted_runs"] == 0
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
