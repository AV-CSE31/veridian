"""
veridian.explain
────────────────
Verification Explanation Engine — human-readable explanations for every
verification decision with structured evidence links.
"""

from veridian.explain.engine import (
    Evidence,
    EvidenceType,
    Explanation,
    ExplanationDetail,
    ExplanationEngine,
)

__all__ = [
    "Evidence",
    "EvidenceType",
    "Explanation",
    "ExplanationDetail",
    "ExplanationEngine",
]
