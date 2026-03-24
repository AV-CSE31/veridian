"""
E-01: Skill Trust Decay Under Environmental Drift

Hypothesis H1:
  Veridian-wrapped skills recover from environmental drift with ≥40%
  fewer silent failures compared to unverified execution.

Method:
  1. Load 100 legal/compliance skills from fixtures.
  2. Simulate "drift" by injecting semantic inconsistencies into 30% of
     structured outputs (status=compliant + violated_policies non-empty,
     or risk_level=LOW + decision=ESCALATE).
  3. Baseline condition (no Veridian): all outputs accepted as-is.
  4. Veridian condition: run SemanticGroundingVerifier on each output.
  5. Measure silent failure rate in each condition.
  6. Compute improvement %.

No LLM calls needed — SemanticGroundingVerifier is deterministic.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

from examples.experiments.shared.config import ExperimentResult, RANDOM_SEED
from examples.experiments.shared.metrics import (
    improvement_pct,
    silent_failure_rate,
    print_result,
)
from examples.experiments.shared.skillnet_client import SkillNetClient


# ── Drift injection ───────────────────────────────────────────────────────────

def inject_drift(structured: dict, rng: random.Random) -> dict:
    """Inject a semantic inconsistency into structured output."""
    drifted = dict(structured)
    drift_type = rng.choice(["status_policy", "risk_decision", "summary_diverge"])

    if drift_type == "status_policy":
        # Class A: status=compliant but violated_policies present
        drifted["status"] = "compliant"
        drifted["violated_policies"] = ["policy_A", "policy_B"]
    elif drift_type == "risk_decision":
        # Class A: risk_level=LOW but decision=ESCALATE
        drifted["risk_level"] = "LOW"
        drifted["decision"] = "ESCALATE"
    else:
        # Class C: summary says "no issues" but risk=HIGH
        drifted["risk_level"] = "HIGH"
        drifted["_summary_drift"] = True  # marker for raw_output manipulation

    return drifted


def build_task_result(skill: dict, drifted: bool = False, rng: random.Random = None) -> tuple[Task, TaskResult]:
    """Build a Task and TaskResult from a skill record."""
    structured = dict(skill["structured_output"])
    raw = f"Completed analysis: {skill['name']}. Result is structured below."

    if drifted and rng:
        structured = inject_drift(structured, rng)
        if structured.pop("_summary_drift", False):
            raw = "No issues found in the analysis. All checks passed."
        raw += " Analysis complete."

    task = Task(
        id=skill["id"],
        title=skill["name"],
        description=f"Verify: {skill['name']}. Output required fields.",
        verifier_id="semantic_grounding",
    )
    result = TaskResult(
        raw_output=raw,
        structured=structured,
    )
    return task, result


# ── Experiment ────────────────────────────────────────────────────────────────

def run() -> ExperimentResult:
    """Run E-01 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)
    client = SkillNetClient()

    # Use legal + compliance skills (semantic grounding is most relevant there)
    skills = (
        client.list_skills(domain="legal", limit=50)
        + client.list_skills(domain="compliance", limit=50)
    )
    rng.shuffle(skills)
    skills = skills[:100]

    # Mark 30% as drifted
    drift_indices = set(rng.sample(range(len(skills)), k=30))

    verifier = SemanticGroundingVerifier(
        check_empty_structured=True,
        check_artifacts_match_summary=True,
    )

    # ── Baseline: no verification (all pass silently) ─────────────────────────
    baseline_silent_failures = 0
    for i, skill in enumerate(skills):
        if i in drift_indices:
            baseline_silent_failures += 1  # drifted output accepted silently

    baseline_sfr = silent_failure_rate(baseline_silent_failures, len(skills))

    # ── Veridian: SemanticGroundingVerifier on each output ────────────────────
    veridian_silent_failures = 0
    caught = 0
    false_positives = 0

    for i, skill in enumerate(skills):
        is_drifted = i in drift_indices
        task, result = build_task_result(skill, drifted=is_drifted, rng=rng)

        verdict = verifier.verify(task, result)

        if is_drifted:
            if verdict.passed:
                # Bad output slipped through — silent failure
                veridian_silent_failures += 1
            else:
                caught += 1
        else:
            if not verdict.passed:
                false_positives += 1

    veridian_sfr = silent_failure_rate(veridian_silent_failures, len(skills))

    # ── Metrics ───────────────────────────────────────────────────────────────
    impv = improvement_pct(baseline_sfr, baseline_sfr - veridian_sfr)
    # H1 requires ≥40% reduction in silent failures
    sfr_reduction = improvement_pct(baseline_sfr, veridian_sfr) * -1

    passed = sfr_reduction >= 40.0

    result_obj = ExperimentResult(
        experiment_id="E-01",
        hypothesis="Veridian reduces silent failures under drift by ≥40%",
        passed=passed,
        primary_metric="silent_failure_rate_reduction_pct",
        primary_value=sfr_reduction,
        threshold=40.0,
        secondary_metrics={
            "baseline_sfr": baseline_sfr,
            "veridian_sfr": veridian_sfr,
            "drifted_tasks": len(drift_indices),
            "caught_by_verifier": caught,
            "false_positives": false_positives,
            "total_tasks": len(skills),
        },
        notes=(
            f"SemanticGroundingVerifier caught {caught}/{len(drift_indices)} "
            f"drifted outputs with {false_positives} false positives."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
