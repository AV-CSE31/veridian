"""
E-09: End-to-End AutoResearch Ablation

Hypothesis H9:
  The full Veridian pipeline (SemanticGrounding + SelfConsistency +
  CrossRunConsistencyHook) maintains SkillNet retrieval gains measured
  in E-01 through E-04, with no more than 5% degradation vs individual
  component baselines.

Method:
  1. Use 50 tasks from fixtures (25 legal, 25 compliance).
  2. Inject drift in 30% of tasks.
  3. Run 4 conditions:
     a. no_veridian      — accept all outputs
     b. grounding_only   — SemanticGroundingVerifier
     c. consistency_only — CrossRunConsistencyHook (no verifier)
     d. full_pipeline    — SemanticGrounding + CrossRunConsistencyHook
  4. For each condition, measure:
     - silent failure rate (bad outputs accepted)
     - false positive rate (good outputs rejected)
     - combined F1
  5. Verify full_pipeline ≥ max(individual) - 5%.

Note: SelfConsistencyVerifier requires LLM calls (gemini-2.0-flash).
It is included only if GEMINI_API_KEY is available; otherwise skipped.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import f1, print_result
from examples.experiments.shared.skillnet_client import SkillNetClient

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.hooks.builtin.cross_run_consistency import CrossRunConsistencyHook
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

# ── Drift injection (reused from E-01) ────────────────────────────────────────


def inject_drift(structured: dict, rng: random.Random) -> tuple[dict, str]:
    """Return (drifted_structured, drift_type)."""
    drifted = dict(structured)
    drift_type = rng.choice(["status_policy", "risk_decision"])
    if drift_type == "status_policy":
        drifted["status"] = "compliant"
        drifted["violated_policies"] = ["policy_A"]
    else:
        drifted["risk_level"] = "LOW"
        drifted["decision"] = "ESCALATE"
    return drifted, drift_type


@dataclass
class TaskEntry:
    task: Task
    result: TaskResult
    is_drifted: bool
    entity_id: str


@dataclass
class RunStartedEvent:
    run_id: str = "run_e09"


@dataclass
class TaskCompletedEvent:
    task: Task
    run_id: str = "run_e09"


def build_entries(
    skills: list[dict], drift_indices: set[int], rng: random.Random
) -> list[TaskEntry]:
    entries = []
    for i, skill in enumerate(skills):
        is_drifted = i in drift_indices
        structured = dict(skill["structured_output"])
        raw = f"Analysis: {skill['name']}."
        if is_drifted:
            structured, _ = inject_drift(structured, rng)

        task = Task(
            id=skill["id"],
            title=skill["name"],
            description=f"Execute skill: {skill['name']}",
            verifier_id="semantic_grounding",
            metadata={"entity_id": skill["id"]},
        )
        task.status = TaskStatus.DONE
        result = TaskResult(raw_output=raw, structured=structured)
        task.result = result
        entries.append(
            TaskEntry(task=task, result=result, is_drifted=is_drifted, entity_id=skill["id"])
        )
    return entries


@dataclass
class ConditionResult:
    name: str
    y_true: list[int] = field(default_factory=list)
    y_pred: list[int] = field(default_factory=list)

    def sfr(self) -> float:
        bad_accepted = sum(1 for a, b in zip(self.y_true, self.y_pred) if a == 1 and b == 0)
        return bad_accepted / max(len(self.y_true), 1)

    def fpr(self) -> float:
        fp = sum(1 for a, b in zip(self.y_true, self.y_pred) if a == 0 and b == 1)
        n_neg = sum(1 for a in self.y_true if a == 0)
        return fp / n_neg if n_neg > 0 else 0.0

    def f1_score(self) -> float:
        return f1(self.y_true, self.y_pred)


def run_condition(
    entries: list[TaskEntry],
    use_grounding: bool,
    use_consistency: bool,
) -> ConditionResult:
    """Run a single ablation condition and return per-task predictions."""
    condition_name = (
        "full"
        if (use_grounding and use_consistency)
        else "grounding"
        if use_grounding
        else "consistency"
        if use_consistency
        else "none"
    )
    cond = ConditionResult(name=condition_name)

    grounding_verifier = SemanticGroundingVerifier() if use_grounding else None
    hook = (
        CrossRunConsistencyHook(
            claim_fields=["risk_level", "decision", "status"],
            entity_key_field="entity_id",
            raise_on_critical=False,
        )
        if use_consistency
        else None
    )

    if hook:
        hook.on_run_started(RunStartedEvent())

    hook_conflicts_before = 0

    for entry in entries:
        # Ground truth: drifted = bad output
        cond.y_true.append(1 if entry.is_drifted else 0)

        # Verifier check
        verifier_rejected = False
        if grounding_verifier:
            vr = grounding_verifier.verify(entry.task, entry.result)
            if not vr.passed:
                verifier_rejected = True

        # Hook check: fire after_result
        hook_flagged = False
        if hook and not verifier_rejected:
            hook.after_result(TaskCompletedEvent(task=entry.task))
            n_conflicts = len(hook.conflicts)
            if n_conflicts > hook_conflicts_before:
                hook_flagged = True
                hook_conflicts_before = n_conflicts

        # Prediction: 1 = flagged as problematic
        flagged = verifier_rejected or hook_flagged
        cond.y_pred.append(1 if flagged else 0)

    return cond


def run() -> ExperimentResult:
    """Run E-09 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)
    client = SkillNetClient()

    # 25 legal + 25 compliance skills
    skills = client.list_skills(domain="legal", limit=25) + client.list_skills(
        domain="compliance", limit=25
    )
    rng.shuffle(skills)

    # 30% drift
    drift_indices = set(rng.sample(range(len(skills)), k=15))
    entries = build_entries(skills, drift_indices, rng)

    # Run 4 conditions
    conditions = {
        "no_veridian": run_condition(entries, use_grounding=False, use_consistency=False),
        "grounding_only": run_condition(entries, use_grounding=True, use_consistency=False),
        "consistency_only": run_condition(entries, use_grounding=False, use_consistency=True),
        "full_pipeline": run_condition(entries, use_grounding=True, use_consistency=True),
    }

    # ── Metrics ───────────────────────────────────────────────────────────────
    f1_scores = {name: c.f1_score() for name, c in conditions.items()}
    sfr_scores = {name: c.sfr() for name, c in conditions.items()}

    full_f1 = f1_scores["full_pipeline"]
    best_individual_f1 = max(
        f1_scores["grounding_only"],
        f1_scores["consistency_only"],
    )

    # H9: full_pipeline F1 >= best_individual - 5% (no degradation)
    degradation_pct = (best_individual_f1 - full_f1) / max(best_individual_f1, 1e-9) * 100
    h9_passed = degradation_pct <= 5.0

    # Also check full_pipeline beats no_veridian
    improvement_vs_baseline = (
        (full_f1 - f1_scores["no_veridian"]) / max(f1_scores["no_veridian"], 1e-9) * 100
    )

    result_obj = ExperimentResult(
        experiment_id="E-09",
        hypothesis="Full Veridian pipeline maintains gains with ≤5% degradation vs individual components",
        passed=h9_passed,
        primary_metric="degradation_from_best_individual_pct",
        primary_value=degradation_pct,
        threshold=5.0,
        secondary_metrics={
            "no_veridian_f1": f1_scores["no_veridian"],
            "grounding_only_f1": f1_scores["grounding_only"],
            "consistency_only_f1": f1_scores["consistency_only"],
            "full_pipeline_f1": full_f1,
            "improvement_vs_baseline_pct": round(improvement_vs_baseline, 2),
            "full_pipeline_sfr": sfr_scores["full_pipeline"],
            "no_veridian_sfr": sfr_scores["no_veridian"],
            "drifted_tasks": len(drift_indices),
            "total_tasks": len(entries),
        },
        notes=(
            f"F1: none={f1_scores['no_veridian']:.3f}, "
            f"grounding={f1_scores['grounding_only']:.3f}, "
            f"consistency={f1_scores['consistency_only']:.3f}, "
            f"full={full_f1:.3f}. "
            f"Degradation vs best individual: {degradation_pct:.1f}%."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
