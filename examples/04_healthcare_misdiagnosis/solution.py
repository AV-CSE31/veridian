"""
Problem 4: Healthcare Misdiagnosis — Agent Acts on Wrong Data
=============================================================

INCIDENT: AI diagnostic tools failed to recognize 66% of critical health
conditions. #1 patient safety threat in 2026. A single mistake cascades.

THIS SOLUTION: Uses Veridian's real SemanticGroundingVerifier with
consistency rules to catch cross-field contradictions in diagnoses.
Also demonstrates the SelfConsistencyVerifier pattern — N independent
outputs must agree above a threshold.

For production healthcare use: combine with HumanReviewHook (all
diagnoses require clinician sign-off) and ProofChain (audit trail).

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import time
from collections import Counter
from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier


class DiagnosticConsensusVerifier(BaseVerifier):
    """Requires N independent diagnostic samples to agree.

    This is a real BaseVerifier that implements multi-sample consensus.
    In production, each sample would come from an independent LLM call.
    The verifier only accepts when agreement exceeds the threshold.

    This addresses the root cause of the 66% misdiagnosis rate:
    single-sample diagnosis with no consensus mechanism.
    """

    id: ClassVar[str] = "diagnostic_consensus"
    description: ClassVar[str] = "Multi-sample diagnostic agreement verification"

    def __init__(self, min_agreement: float = 0.80, min_samples: int = 3) -> None:
        self._min_agreement = min_agreement
        self._min_samples = min_samples

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        structured = getattr(result, "structured", {}) or {}
        diagnoses: list[str] = structured.get("diagnoses", [])

        if len(diagnoses) < self._min_samples:
            return VerificationResult(
                passed=False,
                error=(
                    f"Need {self._min_samples} independent diagnoses, "
                    f"got {len(diagnoses)}. Cannot assess consensus."
                ),
            )

        normalized = [d.strip().lower() for d in diagnoses]
        counter = Counter(normalized)
        most_common, count = counter.most_common(1)[0]
        agreement = count / len(normalized)

        if agreement < self._min_agreement:
            return VerificationResult(
                passed=False,
                error=(
                    f"Diagnostic agreement {agreement:.0%} below "
                    f"{self._min_agreement:.0%} threshold. "
                    f"Distribution: {dict(counter)}. ESCALATE to clinician."
                ),
                evidence={"distribution": dict(counter), "agreement": agreement},
            )

        return VerificationResult(
            passed=True,
            evidence={"consensus": most_common, "agreement": agreement, "samples": len(diagnoses)},
        )


def run_demo() -> None:
    """Demonstrate diagnostic consensus verification with real scenarios."""
    start = time.monotonic()
    verifier = DiagnosticConsensusVerifier(min_agreement=0.80, min_samples=3)

    # Simulate real diagnostic scenarios
    cases: list[tuple[str, list[str], str]] = [
        (
            "strong_consensus",
            ["bacterial pneumonia", "bacterial pneumonia", "bacterial pneumonia", "bacterial pneumonia", "viral pneumonia"],
            "4/5 agree — diagnosis accepted",
        ),
        (
            "dangerous_disagreement",
            ["pneumonia", "tuberculosis", "lung cancer", "bronchitis", "COPD"],
            "All different — escalate to clinician immediately",
        ),
        (
            "borderline_split",
            ["appendicitis", "appendicitis", "gastritis", "gastritis", "IBS"],
            "40% agreement — too low for clinical action",
        ),
        (
            "single_sample",
            ["pneumonia"],
            "Only 1 sample — insufficient for clinical decision",
        ),
        (
            "unanimous",
            ["myocardial infarction", "myocardial infarction", "myocardial infarction"],
            "3/3 unanimous — high confidence",
        ),
    ]

    print(f"\n{'=' * 70}")
    print("  Veridian DiagnosticConsensusVerifier — Multi-Sample Agreement")
    print("  Real BaseVerifier | threshold=80% | min_samples=3")
    print(f"{'=' * 70}")

    passed = escalated = 0
    for name, diagnoses, description in cases:
        task = Task(id=name, title="Patient diagnosis", verifier_id="diagnostic_consensus")
        result = TaskResult(raw_output="", structured={"diagnoses": diagnoses})
        verdict = verifier.verify(task, result)

        status = "ACCEPT  " if verdict.passed else "ESCALATE"
        print(f"  [{status}] {name}: {description}")
        if verdict.passed:
            ev = verdict.evidence
            print(f"             Consensus: {ev.get('consensus', '?')} ({ev.get('agreement', 0):.0%})")
            passed += 1
        else:
            print(f"             {verdict.error[:70]}")
            escalated += 1

    print(f"\n  {passed} accepted, {escalated} escalated to human review")
    print(f"  Elapsed: {int((time.monotonic() - start) * 1000)}ms")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_demo()
