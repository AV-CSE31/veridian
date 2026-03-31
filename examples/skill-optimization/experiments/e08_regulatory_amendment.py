"""
E-08: Regulatory Amendment Propagation

Hypothesis H8:
  EntropyGC detects >=95% of tasks that become stale/invalid after
  regulatory amendments, with zero missed CRITICAL invalidations.

Method:
  1. Load TaskLedger with 60 compliance tasks across 3 frameworks.
  2. Mark 20 tasks as stale IN_PROGRESS (artificially old timestamps,
     simulating tasks stuck after a regulatory amendment).
  3. Mark 5 tasks with excessive retries (>= max_retries_threshold).
  4. Mark 3 tasks as depending on abandoned parents.
  5. Run EntropyGC stub to detect all three entropy types.
  6. Ground truth: 28 tasks (20 stale + 5 excessive_retry + 3 abandoned_dep).
  7. Measure recall and zero-missed-CRITICAL (abandoned chains).

No LLM calls -- EntropyGC is deterministic.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import print_result, recall_score
from examples.experiments.shared.stubs import EntropyGC

from veridian.core.task import Task
from veridian.ledger.ledger import TaskLedger

FRAMEWORKS = ["SOC2", "ISO27001", "NIST-CSF"]
CONTROLS = [f"CC{i}.{j}" for i in range(1, 10) for j in range(1, 4)]

# EntropyGC threshold for staleness (minutes)
STALE_THRESHOLD_MINUTES = 60
# Excessive retries threshold
MAX_RETRIES_THRESHOLD = 3


def _force_stale_timestamps(
    ledger_path: Path,
    task_ids: set[str],
    minutes_ago: int,
) -> None:
    """Directly modify ledger JSON to set status=in_progress and updated_at to the past."""
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    old_time = (datetime.now(tz=UTC) - timedelta(minutes=minutes_ago)).isoformat()
    for tid in task_ids:
        if tid in data["tasks"]:
            data["tasks"][tid]["status"] = "in_progress"
            data["tasks"][tid]["updated_at"] = old_time

    tmp = str(ledger_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(ledger_path))


def _force_retry_count(
    ledger_path: Path,
    task_ids: set[str],
    retry_count: int,
) -> None:
    """Directly modify ledger JSON to set retry_count."""
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    for tid in task_ids:
        if tid in data["tasks"]:
            data["tasks"][tid]["retry_count"] = retry_count
            data["tasks"][tid]["status"] = "failed"  # failed with excessive retries

    tmp = str(ledger_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(ledger_path))


def _force_abandoned(
    ledger_path: Path,
    task_ids: set[str],
) -> None:
    """Directly set tasks to ABANDONED status."""
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    for tid in task_ids:
        if tid in data["tasks"]:
            data["tasks"][tid]["status"] = "abandoned"
            data["tasks"][tid]["last_error"] = "regulatory_control_withdrawn"

    tmp = str(ledger_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(ledger_path))


def run() -> ExperimentResult:
    """Run E-08 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)

    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "ledger.json"
        ledger = TaskLedger(
            path=str(ledger_path),
            progress_file=str(Path(tmp_dir) / "progress.md"),
        )

        # Build 60 compliance tasks
        tasks: list[Task] = []
        for i in range(60):
            fw = rng.choice(FRAMEWORKS)
            ctrl = rng.choice(CONTROLS)
            task = Task(
                id=f"reg_task_{i:03d}",
                title=f"{fw} {ctrl} assessment",
                description=(
                    f"Assess {fw} control {ctrl}. "
                    f"Verify: output status and evidence_source fields."
                ),
                verifier_id="schema",
                verifier_config={"required_fields": ["status"]},
                max_retries=MAX_RETRIES_THRESHOLD,
            )
            tasks.append(task)

        ledger.add(tasks)

        # ── Set up parent tasks (tasks[0], tasks[1]) to be abandoned
        parent_ids = {tasks[0].id, tasks[1].id}

        # ── Set up child tasks to depend on parents (tasks 55-57)
        orphan_children = tasks[55:58]
        orphan_child_ids = {t.id for t in orphan_children}

        # Update depends_on in ledger for children
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        for child in orphan_children:
            if child.id in data["tasks"]:
                data["tasks"][child.id]["depends_on"] = [tasks[0].id]
        with open(str(ledger_path) + ".tmp2", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(str(ledger_path) + ".tmp2", str(ledger_path))

        # ── Mark 20 tasks as stale IN_PROGRESS (tasks 2-21)
        stale_ids = {tasks[i].id for i in range(2, 22)}
        # Force in_progress + old timestamps via direct JSON edit (avoids ledger
        # state machine, which has a ClassVar/Enum metaclass issue under PEP 563)
        _force_stale_timestamps(
            ledger_path, stale_ids,
            minutes_ago=STALE_THRESHOLD_MINUTES + 30,
        )

        # ── Mark 5 tasks with excessive retries (tasks 22-26)
        excessive_retry_ids = {tasks[i].id for i in range(22, 27)}
        _force_retry_count(ledger_path, excessive_retry_ids, retry_count=MAX_RETRIES_THRESHOLD)

        # ── Abandon parent tasks
        _force_abandoned(ledger_path, parent_ids)

        # ── Run EntropyGC (re-open ledger to pick up changes)
        ledger2 = TaskLedger(
            path=str(ledger_path),
            progress_file=str(Path(tmp_dir) / "progress.md"),
        )
        gc = EntropyGC(
            ledger=ledger2,
            stale_threshold_minutes=STALE_THRESHOLD_MINUTES,
            max_retries_threshold=MAX_RETRIES_THRESHOLD,
        )
        issues = gc.run(report_path=str(Path(tmp_dir) / "entropy_report.md"))

        # ── Measure detection ─────────────────────────────────────────────────
        stale_issues = [i for i in issues if i.type == "stale_in_progress"]
        retry_issues = [i for i in issues if i.type == "excessive_retries"]
        abandoned_issues = [i for i in issues if i.type == "abandoned_dependency_chain"]

        detected_stale = {i.task_id for i in stale_issues}
        detected_retry = {i.task_id for i in retry_issues}
        detected_abandoned = {i.task_id for i in abandoned_issues}

        gt_all = stale_ids | excessive_retry_ids | orphan_child_ids
        detected_all = detected_stale | detected_retry | detected_abandoned

        all_task_ids = [t.id for t in tasks]
        y_true_all = [1 if tid in gt_all else 0 for tid in all_task_ids]
        y_pred_all = [1 if tid in detected_all else 0 for tid in all_task_ids]

        rec = recall_score(y_true_all, y_pred_all)

        missed_critical = orphan_child_ids - detected_abandoned
        zero_missed_critical = len(missed_critical) == 0

        h8_passed = rec >= 0.95 and zero_missed_critical

        result_obj = ExperimentResult(
            experiment_id="E-08",
            hypothesis="EntropyGC achieves >=95% recall with zero missed CRITICAL invalidations",
            passed=h8_passed,
            primary_metric="recall",
            primary_value=rec,
            threshold=0.95,
            secondary_metrics={
                "zero_missed_critical": zero_missed_critical,
                "stale_detected": len(detected_stale),
                "stale_ground_truth": len(stale_ids),
                "retry_detected": len(detected_retry),
                "retry_ground_truth": len(excessive_retry_ids),
                "abandoned_detected": len(detected_abandoned),
                "abandoned_ground_truth": len(orphan_child_ids),
                "total_issues_detected": len(issues),
                "entropy_report_written": True,
            },
            notes=(
                f"Stale: {len(detected_stale)}/{len(stale_ids)}. "
                f"Retries: {len(detected_retry)}/{len(excessive_retry_ids)}. "
                f"Abandoned chains: {len(detected_abandoned)}/{len(orphan_child_ids)}. "
                f"Missed critical: {len(missed_critical)}."
            ),
        )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
