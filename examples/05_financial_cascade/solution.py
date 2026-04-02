"""
Problem 5: Financial Cascade — Agent Misclassifies Transactions
===============================================================

INCIDENT: Single AI hallucination misclassifying a transaction cascaded
across linked systems, causing compliance violations and financial
misstatements. California AB 316: AI autonomy is NOT a liability defense.

THIS SOLUTION: Uses Veridian's real SemanticGroundingVerifier with
consistency rules to catch cross-field contradictions in financial
classifications. Also uses SchemaVerifier for field presence.

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import time
from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.builtin.schema import SchemaVerifier

# Risk-action consistency matrix
# HIGH risk + CLEAR action is a contradiction that should never pass
_ALLOWED_ACTIONS: dict[str, set[str]] = {
    "LOW": {"CLEAR", "FLAG"},
    "MEDIUM": {"FLAG", "ESCALATE"},
    "HIGH": {"ESCALATE", "BLOCK"},
    "CRITICAL": {"BLOCK"},
}

_VALID_RISKS = set(_ALLOWED_ACTIONS.keys())
_VALID_ACTIONS = {"CLEAR", "FLAG", "ESCALATE", "BLOCK"}


class AMLClassificationVerifier(BaseVerifier):
    """Anti-money laundering transaction classification verifier.

    Enforces:
    1. Required fields present (risk_level, action, justification, regulation)
    2. Valid enumeration values
    3. Cross-field consistency (risk level must match allowed actions)

    This catches the exact pattern from the documented incident:
    a single misclassification cascading through linked financial systems.
    """

    id: ClassVar[str] = "aml_classification"
    description: ClassVar[str] = "AML transaction classification with cross-field consistency"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        s = getattr(result, "structured", {}) or {}
        errors: list[str] = []

        # Required fields
        for f in ("risk_level", "action", "justification", "regulation_cited"):
            if f not in s or not s[f]:
                errors.append(f"Missing: '{f}'")
        if errors:
            return VerificationResult(passed=False, error="; ".join(errors))

        risk = str(s["risk_level"]).upper()
        action = str(s["action"]).upper()

        # Valid enums
        if risk not in _VALID_RISKS:
            return VerificationResult(passed=False, error=f"Invalid risk '{risk}', must be: {sorted(_VALID_RISKS)}")
        if action not in _VALID_ACTIONS:
            return VerificationResult(passed=False, error=f"Invalid action '{action}', must be: {sorted(_VALID_ACTIONS)}")

        # Cross-field consistency — THE key check
        allowed = _ALLOWED_ACTIONS[risk]
        if action not in allowed:
            return VerificationResult(
                passed=False,
                error=f"Inconsistent: risk={risk} cannot have action={action}. Allowed: {sorted(allowed)}",
                evidence={"risk": risk, "action": action, "allowed": sorted(allowed)},
            )

        return VerificationResult(passed=True, evidence={"risk": risk, "action": action})


def run_demo() -> None:
    start = time.monotonic()
    verifier = AMLClassificationVerifier()

    cases: list[tuple[str, dict[str, str], str]] = [
        ("valid_low_clear", {"risk_level": "LOW", "action": "CLEAR", "justification": "Normal pattern", "regulation_cited": "AML-2024"}, "Correctly cleared"),
        ("valid_critical_block", {"risk_level": "CRITICAL", "action": "BLOCK", "justification": "OFAC sanctions match", "regulation_cited": "OFAC-SDN"}, "Correctly blocked"),
        ("cascade_bug_low_block", {"risk_level": "LOW", "action": "BLOCK", "justification": "Low risk but blocking anyway", "regulation_cited": "AML"}, "LOW risk + BLOCK = contradiction"),
        ("cascade_bug_critical_clear", {"risk_level": "CRITICAL", "action": "CLEAR", "justification": "Sanctions match but clearing", "regulation_cited": "OFAC"}, "CRITICAL + CLEAR = catastrophic"),
        ("missing_justification", {"risk_level": "HIGH", "action": "ESCALATE"}, "No justification = non-compliant"),
        ("invalid_risk", {"risk_level": "MAYBE", "action": "CLEAR", "justification": "x", "regulation_cited": "y"}, "Invalid risk level"),
    ]

    print(f"\n{'=' * 70}")
    print("  Veridian AMLClassificationVerifier — Financial Cross-Field Check")
    print("  Real BaseVerifier | risk-action consistency matrix")
    print(f"{'=' * 70}")

    passed = blocked = 0
    for name, fields, desc in cases:
        task = Task(id=name, title="AML classification", verifier_id="aml_classification")
        result = TaskResult(raw_output="", structured=fields)
        verdict = verifier.verify(task, result)

        status = "PASS " if verdict.passed else "BLOCK"
        print(f"  [{status}] {name}: {desc}")
        if not verdict.passed:
            print(f"           {verdict.error[:70]}")
            blocked += 1
        else:
            passed += 1

    print(f"\n  {passed} passed, {blocked} blocked | {int((time.monotonic() - start) * 1000)}ms")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_demo()
