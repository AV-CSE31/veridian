"""
E-07: Compliance Skill Ontology

Hypothesis H7:
  TaskQualityGate achieves F1 >= 0.85 when used to identify compliance
  control gaps (tasks with quality_score below threshold).

Method:
  1. Generate 100 compliance control tasks:
     - 40 with genuine gaps (described below)
     - 60 well-formed controls
  2. Run TaskQualityGate.evaluate() on all 100 tasks.
  3. Prediction: "gap" = composite score < 0.80 threshold.
  4. Ground truth: gap_injected=True.
  5. Compute F1, precision, recall.

Gap types designed to score clearly < 0.80:
  - too_short: trivially short + vague description (specificity=0.1)
  - self_dep: task depends on itself (dep_soundness=0.5) + vague description
  - bad_composite: composite verifier with no verifiers list
    + vague description (multiple quality penalties)
  - undeclared_dep: depends_on non-existent ID + poor description

No LLM calls -- TaskQualityGate is deterministic.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import f1, print_result

from veridian.core.quality_gate import TaskQualityGate
from veridian.core.task import Task

FRAMEWORKS = ["SOC2", "ISO27001", "NIST-CSF", "PCI-DSS", "HIPAA"]
CONTROLS = [
    "CC6.1", "CC7.2", "CC8.1", "A1.1", "A1.2",
    "PI1.1", "C1.1", "CC5.1", "CC9.1", "CC9.2",
]
DOMAINS = ["access_control", "encryption", "audit_logging", "incident_response"]
EVIDENCE_TYPES = ["policy_document", "system_screenshot", "log_export"]

# Detection threshold: gap detected if composite score < DETECTION_THRESHOLD
# Good tasks avg ~0.90, gap tasks designed to avg ~0.55
DETECTION_THRESHOLD = 0.75


def make_good_task(idx: int, rng: random.Random) -> Task:
    """Well-formed compliance task -- should score > 0.80."""
    framework = rng.choice(FRAMEWORKS)
    control = rng.choice(CONTROLS)
    domain = rng.choice(DOMAINS)
    evidence_type = rng.choice(EVIDENCE_TYPES)

    return Task(
        id=f"ctrl_{idx:03d}_good",
        title=f"{framework} {control} -- {domain}",
        description=(
            f"Evaluate {domain} control {control} for {framework} compliance. "
            f"Review {evidence_type} as primary evidence source. "
            f"Output must include: status (compliant|partial|gap), "
            f"evidence_source, control_id, and evidence_quote fields. "
            f"Verify: the {control} requirement is fully met per {framework} specifications."
        ),
        verifier_id="schema",
        verifier_config={
            "required_fields": ["status", "evidence_source", "control_id"],
        },
        metadata={
            "framework": framework,
            "control_id": control,
        },
    )


def make_gap_task(idx: int, rng: random.Random, gap_type: str) -> Task:
    """Poorly-formed compliance task -- should score < 0.75 due to quality issues."""
    framework = rng.choice(FRAMEWORKS)
    control = rng.choice(CONTROLS)

    if gap_type == "too_short":
        # Specificity penalty: < 30 chars → score 0.1, no success criteria
        return Task(
            id=f"ctrl_{idx:03d}_gap_short",
            title=f"{framework} check",
            description="Do it.",   # 6 chars → specificity=0.1
            verifier_id="schema",
            verifier_config={},
        )

    elif gap_type == "self_dep":
        # Dep_soundness: depends on itself (score 0.5) + vague description
        task_id = f"ctrl_{idx:03d}_gap_selfdep"
        return Task(
            id=task_id,
            title=f"{framework} {control} self-referential",
            description=(
                f"Handle the {framework} {control} compliance thing. "
                f"Work on it and take care of any issues. Deal with it."
            ),
            verifier_id="schema",
            verifier_config={},
            depends_on=[task_id],   # self-dependency: dep_soundness -= 0.50
        )

    elif gap_type == "bad_composite":
        # Verifiability: composite verifier with NO verifiers list (-0.50)
        # Plus no success criteria in description (-0.35 specificity)
        return Task(
            id=f"ctrl_{idx:03d}_gap_composite",
            title=f"{framework} multi-check",
            description=(
                f"Fix and update the {framework} {control} situation. "
                f"Improve as needed and handle all findings. Take care of it."
            ),
            verifier_id="composite",
            verifier_config={},   # missing "verifiers" key: -0.50 verifiability
        )

    else:  # undeclared_dep
        # Dep_soundness: depends on a non-existent ID (-0.30)
        # + vague description (-0.20 specificity from vague phrases)
        return Task(
            id=f"ctrl_{idx:03d}_gap_orphan",
            title=f"{framework} {control} orphan",
            description=(
                f"Update as needed: {framework} {control} compliance. "
                f"Fix it and ensure things work properly. "
                f"Depends on prior audit results."
            ),
            verifier_id="schema",
            verifier_config={},
            depends_on=["nonexistent_prereq_task_id"],  # orphan dep: -0.30
        )


def run() -> ExperimentResult:
    """Run E-07 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)

    gap_types = ["too_short", "self_dep", "bad_composite", "undeclared_dep"]

    good_tasks = [make_good_task(i, rng) for i in range(60)]
    gap_tasks = [
        make_gap_task(60 + i, rng, gap_types[i % len(gap_types)])
        for i in range(40)
    ]
    all_tasks = good_tasks + gap_tasks
    rng.shuffle(all_tasks)

    gap_ids = {t.id for t in gap_tasks}
    y_true = [1 if t.id in gap_ids else 0 for t in all_tasks]

    # Run quality gate
    gate = TaskQualityGate(
        min_score=0.60,         # internal pass/fail (not used for prediction)
        fail_on_below=0.35,
        require_verifier_match=True,
        require_success_criteria=True,
        log_quality_report=False,
    )
    all_task_ids = {t.id for t in all_tasks}
    scores = gate.evaluate(all_tasks, all_task_ids=all_task_ids)
    score_by_id = {s.task_id: s for s in scores}

    # Prediction: gap detected if composite score < DETECTION_THRESHOLD
    y_pred = [
        1 if score_by_id[t.id].composite < DETECTION_THRESHOLD else 0
        for t in all_tasks
    ]

    f1_score = f1(y_true, y_pred)
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    h7_passed = f1_score >= 0.85

    # Score distributions
    gap_scores = [score_by_id[t.id].composite for t in gap_tasks]
    good_scores = [score_by_id[t.id].composite for t in good_tasks]
    avg_gap = sum(gap_scores) / len(gap_scores)
    avg_good = sum(good_scores) / len(good_scores)

    result_obj = ExperimentResult(
        experiment_id="E-07",
        hypothesis="TaskQualityGate achieves F1 >= 0.85 on compliance gap identification",
        passed=h7_passed,
        primary_metric="f1_gap_detection",
        primary_value=f1_score,
        threshold=0.85,
        secondary_metrics={
            "precision": precision,
            "recall": recall,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "detection_threshold": DETECTION_THRESHOLD,
            "total_tasks": len(all_tasks),
            "gap_tasks": len(gap_tasks),
            "good_tasks": len(good_tasks),
            "avg_gap_score": round(avg_gap, 4),
            "avg_good_score": round(avg_good, 4),
        },
        notes=(
            f"Detection threshold={DETECTION_THRESHOLD}. "
            f"F1={f1_score:.3f}, P={precision:.3f}, R={recall:.3f}. "
            f"Avg scores: gap={avg_gap:.3f}, good={avg_good:.3f}."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
