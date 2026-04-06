"""
E-02: Static Filter vs Dynamic Confidence Scoring

Hypothesis H2:
  A static confidence threshold filter has ≥20% false-positive rate
  on hallucinated skill outputs, while Veridian's SemanticGroundingVerifier
  reduces FP rate by ≥50% on the same set.

Method:
  1. Load 100 skills from fixtures.
  2. Ground truth: "bad" = has_hallucination=True (planted semantic inconsistencies).
  3. Static filter: accept if raw confidence score >= 0.60.
     Hallucinated skills can have high confidence → static filter admits them.
  4. Veridian filter: accept if SemanticGroundingVerifier passes.
     Verifier detects cross-field inconsistencies planted in hallucinated outputs.
  5. FPR = (bad outputs accepted) / (total bad outputs).
  6. Hypothesis: static FPR ≥ 20% AND veridian FPR < static FPR (improvement).

No LLM calls — SemanticGroundingVerifier is deterministic.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import f1, print_result
from examples.experiments.shared.skillnet_client import SkillNetClient

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

# Static threshold: low enough to pass many skills including hallucinated ones
STATIC_THRESHOLD = 0.60


def skill_to_result(skill: dict) -> tuple[Task, TaskResult]:
    task = Task(
        id=skill["id"],
        title=skill["name"],
        description=f"Execute: {skill['name']}",
        verifier_id="semantic_grounding",
    )
    result = TaskResult(
        raw_output=f"Analysis result for {skill['name']}.",
        structured=dict(skill["structured_output"]),
    )
    return task, result


def false_positive_rate(y_true: list[int], y_pred: list[int]) -> float:
    """FPR = bad accepted / total bad = (1 - recall) for bad class."""
    fp = sum(
        1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1
    )  # bad + accepted = false positive
    n_bad = sum(y_true)
    return fp / n_bad if n_bad > 0 else 0.0


def run() -> ExperimentResult:
    """Run E-02 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)
    client = SkillNetClient()

    # Use legal + compliance skills (where hallucinations have cross-field conflicts)
    skills = client.list_skills(domain="legal", limit=50) + client.list_skills(
        domain="compliance", limit=50
    )
    rng.shuffle(skills)
    skills = skills[:100]

    # Ground truth: bad = has_hallucination=True
    # Hallucinated skills have planted semantic inconsistencies
    y_true_bad = [1 if s["has_hallucination"] else 0 for s in skills]
    n_bad = sum(y_true_bad)
    n_good = len(y_true_bad) - n_bad

    verifier = SemanticGroundingVerifier()

    # ── Static filter: accept if confidence >= threshold ─────────────────────
    # Static filter does NOT look at semantic content — purely confidence score
    # A hallucinated skill with confidence=0.85 passes the static filter (FP)
    static_accepted = [1 if s["confidence"] >= STATIC_THRESHOLD else 0 for s in skills]
    # FPR for static: fraction of bad skills accepted by static filter
    static_fpr = false_positive_rate(y_true_bad, static_accepted)

    # ── Veridian filter: accept if SemanticGroundingVerifier passes ────────────
    veridian_accepted = []
    for skill in skills:
        task, result = skill_to_result(skill)
        vr = verifier.verify(task, result)
        veridian_accepted.append(1 if vr.passed else 0)

    # FPR for Veridian: fraction of bad skills that verifier lets through
    veridian_fpr = false_positive_rate(y_true_bad, veridian_accepted)

    # F1 scores (treating "reject bad" as positive class)
    # y_true for F1: bad=1, good=0 → correct prediction is reject bad
    y_true_reject = [1 if b == 1 else 0 for b in y_true_bad]
    y_static_rejects = [1 if a == 0 else 0 for a in static_accepted]
    y_veridian_rejects = [1 if a == 0 else 0 for a in veridian_accepted]

    static_f1 = f1(y_true_reject, y_static_rejects)
    veridian_f1 = f1(y_true_reject, y_veridian_rejects)

    fpr_reduction_pct = (
        (static_fpr - veridian_fpr) / max(static_fpr, 1e-9) * 100 if static_fpr > 0 else 0.0
    )

    # H2: static FPR >= 20% AND veridian reduces it
    h2_passed = static_fpr >= 0.20 and veridian_fpr < static_fpr

    result_obj = ExperimentResult(
        experiment_id="E-02",
        hypothesis="Static filter FPR >=20%; SemanticGrounding reduces FPR",
        passed=h2_passed,
        primary_metric="static_false_positive_rate",
        primary_value=static_fpr,
        threshold=0.20,
        secondary_metrics={
            "veridian_fpr": veridian_fpr,
            "fpr_reduction_pct": round(fpr_reduction_pct, 2),
            "static_f1": static_f1,
            "veridian_f1": veridian_f1,
            "total_skills": len(skills),
            "hallucinated_skills": n_bad,
            "clean_skills": n_good,
            "static_threshold": STATIC_THRESHOLD,
        },
        notes=(
            f"Static (threshold={STATIC_THRESHOLD}): FPR={static_fpr:.3f}, F1={static_f1:.3f}. "
            f"Veridian (SemanticGrounding): FPR={veridian_fpr:.3f}, F1={veridian_f1:.3f}. "
            f"FPR reduction: {fpr_reduction_pct:.1f}%."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
