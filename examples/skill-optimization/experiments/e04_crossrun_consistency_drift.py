"""
E-04: CrossRunConsistencyHook as Drift Detector

Hypothesis H4:
  CrossRunConsistencyHook achieves AUROC ≥ 0.85 when used as a binary
  classifier to distinguish drifted vs stable task outputs.

Method:
  1. Create 80 synthetic task completions for 20 entities (4 tasks each).
  2. Plant contradictions in 40% of follow-up tasks (same entity,
     conflicting risk_level/decision/status).
  3. Run CrossRunConsistencyHook.after_result() on each task in order.
  4. Hook's conflict detection gives us a binary prediction (conflict=1, clean=0).
  5. Ground truth: drifted task = 1.
  6. Compute AUROC using conflict severity as the score.

No LLM calls — CrossRunConsistencyHook is deterministic.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import auroc, print_result

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.hooks.builtin.cross_run_consistency import CrossRunConsistencyHook

# ── Minimal event stub ────────────────────────────────────────────────────────


@dataclass
class TaskCompletedEvent:
    task: Task
    run_id: str = "run_e04"


@dataclass
class RunStartedEvent:
    run_id: str = "run_e04"


# ── Data generation ───────────────────────────────────────────────────────────

RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
DECISIONS = ["ALLOW", "FLAG", "ESCALATE", "REMOVE"]
STATUSES = ["compliant", "partial", "gap"]


def generate_entity_tasks(
    entity_id: str,
    n_tasks: int,
    drift_indices: set[int],
    rng: random.Random,
    base_risk: str,
    base_decision: str,
    base_status: str,
) -> list[dict]:
    """Generate n_tasks for one entity, injecting contradictions at drift_indices."""
    tasks = []
    for i in range(n_tasks):
        if i in drift_indices:
            # Contradiction: flip to opposite value
            risk = rng.choice([r for r in RISK_LEVELS if r != base_risk])
            decision = rng.choice([d for d in DECISIONS if d != base_decision])
            status = rng.choice([s for s in STATUSES if s != base_status])
            is_drifted = True
        else:
            risk = base_risk
            decision = base_decision
            status = base_status
            is_drifted = False

        task = Task(
            id=f"{entity_id}_task_{i:02d}",
            title=f"Evaluate entity {entity_id} (run {i})",
            description=f"Assess risk for entity {entity_id}.",
            verifier_id="semantic_grounding",
            metadata={"entity_id": entity_id},
        )
        task.status = TaskStatus.DONE
        task.result = TaskResult(
            raw_output=f"Analysis complete for {entity_id}.",
            structured={
                "risk_level": risk,
                "decision": decision,
                "status": status,
                "entity_id": entity_id,
            },
        )
        tasks.append({"task": task, "is_drifted": is_drifted, "entity_id": entity_id})
    return tasks


def run() -> ExperimentResult:
    """Run E-04 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)

    n_entities = 20
    tasks_per_entity = 4
    # 40% of follow-up tasks (tasks 1-3) will be drifted
    drift_probability = 0.40

    hook = CrossRunConsistencyHook(
        claim_fields=["risk_level", "decision", "status"],
        entity_key_field="entity_id",
        raise_on_critical=False,
    )

    # Simulate run start
    hook.on_run_started(RunStartedEvent())

    all_entries: list[dict] = []

    for e_idx in range(n_entities):
        entity_id = f"entity_{e_idx:03d}"
        base_risk = rng.choice(RISK_LEVELS)
        base_decision = "ALLOW" if base_risk in ("LOW", "MEDIUM") else "ESCALATE"
        base_status = rng.choice(STATUSES)

        # Task 0 is always clean (establishes baseline)
        # Tasks 1–3 may drift
        drift_in_this_entity = {
            i for i in range(1, tasks_per_entity) if rng.random() < drift_probability
        }

        entries = generate_entity_tasks(
            entity_id,
            tasks_per_entity,
            drift_in_this_entity,
            rng,
            base_risk,
            base_decision,
            base_status,
        )
        all_entries.extend(entries)

    # Process tasks through hook in order
    y_true = []  # 1 = ground-truth drifted
    scores = []  # hook conflict score (0.0 = no conflict, 1.0 = critical conflict)

    prev_conflict_count = 0
    for entry in all_entries:
        task = entry["task"]
        is_drifted = entry["is_drifted"]

        event = TaskCompletedEvent(task=task)
        hook.after_result(event)

        # Score: did the hook detect a new conflict after this task?
        new_conflicts = len(hook.conflicts) - prev_conflict_count
        prev_conflict_count = len(hook.conflicts)

        # Use conflict severity as continuous score
        if new_conflicts > 0:
            latest = hook.conflicts[-1]
            score = 1.0 if latest.severity == "critical" else 0.6
        else:
            score = 0.0

        y_true.append(1 if is_drifted else 0)
        scores.append(score)

    auc = auroc(y_true, scores)
    h4_passed = auc >= 0.85

    # Additional stats
    total_drifted = sum(y_true)
    hook_summary = hook.summary()

    result_obj = ExperimentResult(
        experiment_id="E-04",
        hypothesis="CrossRunConsistencyHook achieves AUROC ≥ 0.85 as drift detector",
        passed=h4_passed,
        primary_metric="auroc",
        primary_value=auc,
        threshold=0.85,
        secondary_metrics={
            "total_tasks": len(all_entries),
            "drifted_tasks": total_drifted,
            "total_conflicts_detected": hook_summary["total_conflicts"],
            "critical_conflicts": hook_summary["critical_conflicts"],
            "entities_tracked": hook_summary["entities_tracked"],
        },
        notes=(
            f"Hook detected {hook_summary['total_conflicts']} conflicts "
            f"({hook_summary['critical_conflicts']} critical) across "
            f"{hook_summary['entities_tracked']} entities."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
