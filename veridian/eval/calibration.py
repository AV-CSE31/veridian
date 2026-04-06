"""
veridian.eval.calibration
──────────────────────────
CalibrationProfile — evaluator configuration for the AdversarialEvaluator.

A CalibrationProfile controls:
  - skepticism: how critical the evaluator is (0.0 = lenient, 1.0 = very strict)
  - rubric: weighted evaluation criteria (weights must sum to 1.0)
  - pass_threshold: minimum weighted score to pass (0.0 < threshold ≤ 1.0)
  - few_shot_examples: optional calibration examples for LLM prompt

Research basis: Anthropic's harness design research shows that out-of-box LLM
evaluators perform poorly. Calibration via rubrics, few-shot examples, and
tuned skepticism significantly improves evaluator-human agreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veridian.core.exceptions import CalibrationError

__all__ = ["RubricCriterion", "GradingRubric", "CalibrationProfile"]

_WEIGHT_TOLERANCE = 1e-9


@dataclass
class RubricCriterion:
    """A single weighted dimension in a GradingRubric."""

    name: str
    description: str
    weight: float  # 0.0–1.0; all weights in a rubric must sum to 1.0


@dataclass
class GradingRubric:
    """
    Multi-criterion evaluation rubric.

    All criterion weights must sum to 1.0 (validated at CalibrationProfile
    construction time — not at GradingRubric construction to allow incremental
    assembly in user code).
    """

    name: str
    criteria: list[RubricCriterion] = field(default_factory=list)

    def validate_weights(self) -> bool:
        """Return True if criterion weights sum to 1.0 (within tolerance)."""
        if not self.criteria:
            return False
        total = sum(c.weight for c in self.criteria)
        return abs(total - 1.0) < _WEIGHT_TOLERANCE

    @property
    def criterion_names(self) -> list[str]:
        """Return ordered list of criterion names."""
        return [c.name for c in self.criteria]


@dataclass
class CalibrationProfile:
    """
    Evaluator calibration configuration.

    Injected into AdversarialEvaluator at construction time.
    Validated eagerly — raises CalibrationError on invalid config so that
    misconfiguration fails fast rather than silently at evaluation time.
    """

    skepticism: float  # 0.0 (lenient) – 1.0 (maximally critical)
    rubric: GradingRubric
    pass_threshold: float  # minimum weighted score to pass
    few_shot_examples: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (0.0 <= self.skepticism <= 1.0):
            raise CalibrationError(f"skepticism must be in [0.0, 1.0], got {self.skepticism}")
        if not (0.0 < self.pass_threshold <= 1.0):
            raise CalibrationError(
                f"pass_threshold must be in (0.0, 1.0], got {self.pass_threshold}"
            )
        if not self.rubric.validate_weights():
            total = sum(c.weight for c in self.rubric.criteria)
            raise CalibrationError(
                f"Rubric '{self.rubric.name}' criterion weights must sum to 1.0, "
                f"got {total:.4f}. "
                f"Adjust weights so they sum exactly to 1.0."
            )

    def compute_weighted_score(self, criterion_scores: dict[str, float]) -> float:
        """
        Compute the aggregate weighted score from per-criterion scores.

        Raises CalibrationError if any rubric criterion is missing from the
        provided scores dict.
        """
        missing = [c.name for c in self.rubric.criteria if c.name not in criterion_scores]
        if missing:
            raise CalibrationError(
                f"Missing criterion scores for: {missing}. "
                f"Expected all of: {self.rubric.criterion_names}"
            )
        return sum(criterion_scores[c.name] * c.weight for c in self.rubric.criteria)

    @classmethod
    def default(cls) -> CalibrationProfile:
        """
        Return a balanced default profile suitable for general-purpose evaluation.

        Criteria: correctness (0.5), quality (0.3), completeness (0.2).
        Skepticism: 0.5. Pass threshold: 0.7.
        """
        rubric = GradingRubric(
            name="general",
            criteria=[
                RubricCriterion(
                    name="correctness",
                    description="Output is factually correct and meets the task specification",
                    weight=0.5,
                ),
                RubricCriterion(
                    name="quality",
                    description="Output is well-structured, clear, and professionally presented",
                    weight=0.3,
                ),
                RubricCriterion(
                    name="completeness",
                    description="All required deliverables are present and complete",
                    weight=0.2,
                ),
            ],
        )
        return cls(skepticism=0.5, rubric=rubric, pass_threshold=0.7)
