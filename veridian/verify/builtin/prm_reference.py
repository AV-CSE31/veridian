"""
veridian.verify.builtin.prm_reference
------------------------------------
Deterministic reference Process Reward Model verifier.

This implementation is intentionally simple and dependency-free so it can act
as a stable baseline for upcoming PRM lifecycle and policy work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from veridian.core.task import PRMBudget, PRMRunResult, PRMScore, TraceStep
from veridian.verify.base import PRMVerifier

__all__ = ["PRMReferenceVerifier"]


@dataclass(frozen=True)
class _StepSignals:
    positive: int = 0
    negative: int = 0
    uncertainty: int = 0


class PRMReferenceVerifier(PRMVerifier):
    """
    Deterministic heuristic PRM verifier.

    The verifier assigns per-step scores from trace content and action type.
    It never calls external services and is fully replay-safe for a fixed input.
    """

    id: ClassVar[str] = "prm_reference"
    description: ClassVar[str] = "Deterministic heuristic PRM verifier for trace-step scoring."

    def __init__(
        self,
        threshold: float = 0.72,
        min_confidence: float = 0.65,
    ) -> None:
        self.threshold = threshold
        self.min_confidence = min_confidence

    def score_steps(
        self,
        *,
        task_id: str,
        steps: list[TraceStep],
        context: dict[str, Any],
        budget: PRMBudget,
    ) -> PRMRunResult:
        scored_steps: list[PRMScore] = []
        for index, step in enumerate(steps, start=1):
            score, confidence, failure_mode = self._score_step(step, context=context)
            scored_steps.append(
                PRMScore(
                    step_id=step.step_id or f"{task_id}_{index}",
                    score=score,
                    confidence=confidence,
                    model_id="prm_reference",
                    version="1",
                    failure_mode=failure_mode,
                )
            )

        if scored_steps:
            aggregate_score = round(
                sum(score.score for score in scored_steps) / len(scored_steps), 3
            )
            aggregate_confidence = round(
                sum(score.confidence for score in scored_steps) / len(scored_steps),
                3,
            )
        else:
            aggregate_score = 0.0
            aggregate_confidence = 0.0

        passed = aggregate_score >= self.threshold and aggregate_confidence >= self.min_confidence
        failure_mode = None
        if not passed:
            failure_mode = self._derive_failure_mode(scored_steps)

        repair_hint = None
        if failure_mode:
            repair_hint = (
                "Add clearer, more specific reasoning and remove uncertain or contradictory steps."
            )

        return PRMRunResult(
            passed=passed,
            aggregate_score=aggregate_score,
            aggregate_confidence=aggregate_confidence,
            threshold=self.threshold,
            scored_steps=scored_steps,
            policy_action="allow",
            repair_hint=repair_hint,
            error=failure_mode,
        )

    def _score_step(
        self,
        step: TraceStep,
        *,
        context: dict[str, Any],
    ) -> tuple[float, float, str | None]:
        content = step.content.strip().lower()
        signals = self._extract_signals(content)

        score = 0.5
        score += signals.positive * 0.12
        score -= signals.negative * 0.18
        score -= signals.uncertainty * 0.08

        if step.action_type in {"plan", "reason", "finalize", "verify"}:
            score += 0.05
        if step.role in {"assistant", "verifier"}:
            score += 0.03
        if len(content) >= 48:
            score += 0.04
        if any(token.isdigit() for token in content):
            score += 0.02

        if "task_id" in context and context["task_id"] == step.step_id:
            score += 0.0

        score = self._clamp(score)

        confidence = 0.4 + signals.positive * 0.1
        confidence -= signals.negative * 0.06
        confidence -= signals.uncertainty * 0.05
        confidence += min(len(content) / 250.0, 0.15)
        confidence += 0.05 if step.action_type in {"verify", "finalize"} else 0.0
        confidence = self._clamp(confidence)

        failure_mode = None
        if signals.negative > 0 and signals.negative >= signals.positive:
            failure_mode = "trace contains more contradictory or error-like signals than evidence of completion"
        elif signals.uncertainty > 0:
            failure_mode = (
                "trace contains uncertain language that should be replaced with concrete results"
            )

        return round(score, 3), round(confidence, 3), failure_mode

    def _extract_signals(self, content: str) -> _StepSignals:
        positive_terms = (
            "complete",
            "completed",
            "done",
            "implemented",
            "verified",
            "fixed",
            "resolved",
            "success",
            "successful",
            "accurate",
            "confident",
            "deterministic",
        )
        negative_terms = (
            "error",
            "failed",
            "fail",
            "broken",
            "incorrect",
            "contradiction",
            "unknown",
            "missing",
            "todo",
        )
        uncertainty_terms = (
            "maybe",
            "might",
            "uncertain",
            "guess",
            "approx",
            "possibly",
            "unsure",
            "not sure",
        )

        positive = sum(1 for term in positive_terms if term in content)
        negative = sum(1 for term in negative_terms if term in content)
        uncertainty = sum(1 for term in uncertainty_terms if term in content)
        return _StepSignals(positive=positive, negative=negative, uncertainty=uncertainty)

    def _derive_failure_mode(self, scored_steps: list[PRMScore]) -> str | None:
        if not scored_steps:
            return "no trace steps were provided for PRM scoring"
        lowest = min(scored_steps, key=lambda item: item.score)
        if lowest.failure_mode:
            return lowest.failure_mode
        return "aggregate PRM score fell below the configured threshold"

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))
