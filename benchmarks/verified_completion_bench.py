#!/usr/bin/env python3
"""
Verified-completion benchmark: claimed-done vs actually-done.

Simulates a worker with "victory declaration bias" --- it always *claims*
success in prose, but with probability ``--defect-rate`` its structured
output violates the task contract (missing field or out-of-contract value).
The same task stream is then driven through two harnesses:

* baseline: trusts the claim --- every submitted result is marked DONE
* gated:    Veridian's contract --- the schema verifier must pass before
            DONE; failures become FAILED instead

Reported metrics:
* baseline.false_done / false_done_rate --- defective results that reached
  DONE because nothing checked the claim (expected ~= defect rate)
* gated.false_done --- defective results that still reached DONE (expected 0)
* gated.caught --- defective results converted to FAILED by the verifier

Usage:
    python benchmarks/verified_completion_bench.py --tasks 200 --defect-rate 0.25
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from pathlib import Path
from typing import Any

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.verify.base import registry

CONTRACT: dict[str, Any] = {
    "required": ["status", "artifact"],
    "properties": {
        "status": {"type": "string", "enum": ["complete"]},
        "artifact": {"type": "string"},
    },
}

CLAIM = "Task completed successfully. All acceptance criteria are met."


def _make_result(rng: random.Random, defect_rate: float) -> tuple[TaskResult, bool]:
    """Return (result, is_defective). The prose claim is identical either way."""
    defective = rng.random() < defect_rate
    if not defective:
        structured = {"status": "complete", "artifact": f"out/report-{rng.randrange(10**6)}.md"}
    elif rng.random() < 0.5:
        structured = {"status": "complete"}  # artifact silently missing
    else:
        structured = {"status": "in_progress", "artifact": "out/report.md"}  # wrong enum
    return TaskResult(raw_output=CLAIM, structured=structured), defective


def _drive(
    ledger: TaskLedger,
    results: list[tuple[TaskResult, bool]],
    gated: bool,
) -> dict[str, Any]:
    verifier = registry.get("schema", {"schema": CONTRACT})
    false_done = 0
    caught = 0
    for i, (result, defective) in enumerate(results):
        task = Task(title=f"bench-{i}", description="verified-completion benchmark task")
        ledger.add([task])
        ledger.claim(task.id, runner_id="bench-worker")
        ledger.submit_result(task.id, result)
        if gated:
            verification = verifier.verify(task, result)
            if verification.passed:
                ledger.mark_done(task.id, result)
            else:
                ledger.mark_failed(task.id, verification.error or "verification failed")
                if defective:
                    caught += 1
        else:
            ledger.mark_done(task.id, result)
        if defective and ledger.get(task.id).status is TaskStatus.DONE:
            false_done += 1

    done = sum(1 for t in ledger.list() if t.status is TaskStatus.DONE)
    return {
        "done": done,
        "false_done": false_done,
        "false_done_rate": round(false_done / len(results), 4) if results else 0.0,
        **({"caught": caught} if gated else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=200)
    parser.add_argument("--defect-rate", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    results = [_make_result(rng, args.defect_rate) for _ in range(args.tasks)]
    defective = sum(1 for _, d in results if d)

    with tempfile.TemporaryDirectory(prefix="veridian-vc-bench-") as td:
        baseline = _drive(
            TaskLedger(path=Path(td) / "baseline.json", progress_file=str(Path(td) / "p1.md")),
            results,
            gated=False,
        )
        gated = _drive(
            TaskLedger(path=Path(td) / "gated.json", progress_file=str(Path(td) / "p2.md")),
            results,
            gated=True,
        )

    report = {
        "benchmark": "verified_completion",
        "tasks": args.tasks,
        "defect_rate_configured": args.defect_rate,
        "defective_results": defective,
        "baseline": baseline,
        "gated": gated,
        "passed": gated["false_done"] == 0 and gated.get("caught") == defective,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
