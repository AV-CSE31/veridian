"""
E-03: SemanticGroundingVerifier on Retrieval Quality

Hypothesis H3:
  Applying SemanticGroundingVerifier to retrieved skill outputs improves
  precision by >=25% on out-of-distribution (OOD) queries compared to
  a naive pass-through baseline.

Method:
  1. Load 100 queries (50 in-dist, 50 OOD) from fixtures.
  2. For each query, simulate a retrieved skill result:
     - in-dist: match to clean skill in same domain (mostly correct)
     - OOD: 65% are semantically corrupted (cross-domain contamination),
            35% are clean but retrieved for an OOD query
  3. Baseline: accept all retrieved results without verification.
  4. Veridian: filter with SemanticGroundingVerifier — reject if fails.
  5. Ground truth: result is correct if skill is NOT corrupted.
  6. Measure precision = TP / (TP+FP) on OOD queries.
     Precision improvement = how much better Veridian does vs accept-all.
  7. Compute improvement %.

No LLM calls -- SemanticGroundingVerifier is deterministic.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import improvement_pct, print_result
from examples.experiments.shared.skillnet_client import SkillNetClient

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

# Fraction of OOD retrievals that are corrupted (semantically inconsistent)
OOD_CORRUPTION_RATE = 0.65


def corrupt_structured(structured: dict, rng: random.Random) -> dict:
    """Inject a semantic inconsistency into structured output."""
    drifted = dict(structured)
    if "status" in drifted:
        drifted["status"] = "compliant"
        drifted["violated_policies"] = ["ood_policy_contamination"]
    elif "risk_level" in drifted:
        drifted["risk_level"] = "LOW"
        drifted["decision"] = "ESCALATE"
    elif "decision" in drifted:
        drifted["decision"] = "ALLOW"
        drifted["violated_policies"] = ["cross_domain_error"]
    return drifted


def simulate_retrieval(
    query: dict,
    skills: list[dict],
    rng: random.Random,
) -> dict:
    """Simulate retrieving a skill for a query."""
    domain = query.get("domain", "legal")
    domain_skills = [s for s in skills if s["domain"] == domain]
    if not domain_skills:
        domain_skills = skills

    if query["distribution"] == "in_dist":
        # In-distribution: pick a clean skill from same domain
        clean = [s for s in domain_skills if not s.get("has_hallucination")]
        skill = rng.choice(clean) if clean else rng.choice(domain_skills)
        structured = dict(skill["structured_output"])
        return {**skill, "structured_output": structured, "is_corrupted": False}
    else:
        # OOD: corrupt OOD_CORRUPTION_RATE fraction; rest are clean retrieval
        is_corrupted = rng.random() < OOD_CORRUPTION_RATE
        if is_corrupted:
            # Pick any skill (including hallucinated) and corrupt it further
            skill = rng.choice(domain_skills)
            corrupted = corrupt_structured(dict(skill["structured_output"]), rng)
            return {**skill, "structured_output": corrupted, "is_corrupted": True}
        else:
            # Clean retrieval even for OOD query
            clean = [s for s in domain_skills if not s.get("has_hallucination")]
            skill = rng.choice(clean) if clean else rng.choice(domain_skills)
            return {
                **skill,
                "structured_output": dict(skill["structured_output"]),
                "is_corrupted": False,
            }


def build_result(skill: dict, query: dict) -> tuple[Task, TaskResult]:
    task = Task(
        id=query["id"],
        title=f"Retrieve: {query['text'][:60]}",
        description=f"Retrieve and verify skill result for: {query['text']}",
        verifier_id="semantic_grounding",
    )
    result = TaskResult(
        raw_output=f"Retrieved result. {skill.get('description', '')}",
        structured=dict(skill["structured_output"]),
    )
    return task, result


def precision(y_true: list[int], y_pred: list[int]) -> float:
    """Precision: TP / (TP + FP). y_pred=1 means accepted."""
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    return tp / (tp + fp) if (tp + fp) > 0 else 0.0


def run() -> ExperimentResult:
    """Run E-03 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)
    client = SkillNetClient()

    in_dist_queries = client.get_queries(distribution="in_dist", limit=50)
    ood_queries = client.get_queries(distribution="ood", limit=50)

    skills = client.list_skills(limit=500)
    verifier = SemanticGroundingVerifier()

    # Ground truth: correct = not corrupted
    def process_queries(queries: list[dict]) -> dict:
        y_true, baseline_pred, veridian_pred = [], [], []
        n_corrupted = 0
        for query in queries:
            retrieved = simulate_retrieval(query, skills, rng)
            task, result = build_result(retrieved, query)
            vr = verifier.verify(task, result)

            gt = 0 if retrieved["is_corrupted"] else 1
            y_true.append(gt)
            baseline_pred.append(1)  # baseline: accept all
            veridian_pred.append(1 if vr.passed else 0)
            if retrieved["is_corrupted"]:
                n_corrupted += 1
        return {
            "y_true": y_true,
            "baseline_pred": baseline_pred,
            "veridian_pred": veridian_pred,
            "n_corrupted": n_corrupted,
        }

    ood_data = process_queries(ood_queries)
    in_dist_data = process_queries(in_dist_queries)

    baseline_ood_prec = precision(ood_data["y_true"], ood_data["baseline_pred"])
    veridian_ood_prec = precision(ood_data["y_true"], ood_data["veridian_pred"])

    baseline_in_dist_prec = precision(in_dist_data["y_true"], in_dist_data["baseline_pred"])
    veridian_in_dist_prec = precision(in_dist_data["y_true"], in_dist_data["veridian_pred"])

    impv = improvement_pct(baseline_ood_prec, veridian_ood_prec)
    h3_passed = impv >= 25.0

    result_obj = ExperimentResult(
        experiment_id="E-03",
        hypothesis="SemanticGrounding improves OOD retrieval precision by >=25%",
        passed=h3_passed,
        primary_metric="ood_precision_improvement_pct",
        primary_value=impv,
        threshold=25.0,
        secondary_metrics={
            "baseline_ood_precision": baseline_ood_prec,
            "veridian_ood_precision": veridian_ood_prec,
            "ood_corrupted": ood_data["n_corrupted"],
            "in_dist_baseline_precision": baseline_in_dist_prec,
            "in_dist_veridian_precision": veridian_in_dist_prec,
            "ood_queries": len(ood_queries),
            "in_dist_queries": len(in_dist_queries),
            "ood_corruption_rate": OOD_CORRUPTION_RATE,
        },
        notes=(
            f"OOD precision: {baseline_ood_prec:.3f} -> {veridian_ood_prec:.3f} "
            f"(+{impv:.1f}%). "
            f"In-dist: {baseline_in_dist_prec:.3f} -> {veridian_in_dist_prec:.3f}."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
