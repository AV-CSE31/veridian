"""
veridian.eval.comparator
────────────────────────
EvolutionComparator — Bayesian A/B comparison of RunSnapshots.

Compares two agent versions across multiple metrics and produces
a recommendation: UPGRADE / HOLD / ROLLBACK with confidence interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veridian.hooks.builtin.drift_detector import RunSnapshot

__all__ = ["EvolutionComparator", "ComparisonResult", "MetricComparison"]


@dataclass
class MetricComparison:
    """Comparison of a single metric between version A and B."""

    metric: str = ""
    value_a: float = 0.0
    value_b: float = 0.0
    delta: float = 0.0
    direction: str = ""  # "improved" | "degraded" | "unchanged"
    magnitude: float = 0.0  # abs(delta) / max(value_a, 0.001)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "metric": self.metric,
            "value_a": round(self.value_a, 4),
            "value_b": round(self.value_b, 4),
            "delta": round(self.delta, 4),
            "direction": self.direction,
            "magnitude": round(self.magnitude, 4),
        }


@dataclass
class ComparisonResult:
    """Aggregated comparison between two agent versions."""

    version_a_id: str = ""
    version_b_id: str = ""
    recommendation: str = "hold"  # "upgrade" | "hold" | "rollback"
    confidence: float = 0.0
    metric_comparisons: dict[str, MetricComparison] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "version_a_id": self.version_a_id,
            "version_b_id": self.version_b_id,
            "recommendation": self.recommendation,
            "confidence": round(self.confidence, 4),
            "metric_comparisons": {k: v.to_dict() for k, v in self.metric_comparisons.items()},
            "reason": self.reason,
        }

    def to_markdown(self) -> str:
        """Generate comparison markdown."""
        lines = [
            f"# Evolution Comparison: {self.version_a_id} vs {self.version_b_id}",
            "",
            f"**Recommendation:** {self.recommendation.upper()}",
            f"**Confidence:** {self.confidence:.2%}",
            f"**Reason:** {self.reason}",
            "",
            "## Metric Breakdown",
            "",
            "| Metric | Version A | Version B | Delta | Direction |",
            "|--------|-----------|-----------|-------|-----------|",
        ]
        for mc in self.metric_comparisons.values():
            lines.append(
                f"| {mc.metric} | {mc.value_a:.4f} | {mc.value_b:.4f} "
                f"| {mc.delta:+.4f} | {mc.direction} |"
            )
        lines.append("")
        return "\n".join(lines)


class EvolutionComparator:
    """Bayesian A/B comparison of two RunSnapshots.

    Compares completion_rate, confidence_mean, retry_rate, mean_tokens_per_task.
    Produces UPGRADE / HOLD / ROLLBACK recommendation.
    """

    def __init__(
        self,
        upgrade_threshold: float = 0.05,
        rollback_threshold: float = 0.10,
    ) -> None:
        self._upgrade_threshold = upgrade_threshold
        self._rollback_threshold = rollback_threshold

    def compare(self, snap_a: RunSnapshot, snap_b: RunSnapshot) -> ComparisonResult:
        """Compare version A vs version B across key metrics."""
        comparisons: dict[str, MetricComparison] = {}

        # Metrics where higher is better
        for metric, val_a, val_b in [
            ("completion_rate", snap_a.completion_rate, snap_b.completion_rate),
            ("confidence_mean", snap_a.confidence_mean, snap_b.confidence_mean),
        ]:
            comparisons[metric] = self._compare_metric(metric, val_a, val_b, higher_is_better=True)

        # Metrics where lower is better
        for metric, val_a, val_b in [
            ("retry_rate", snap_a.retry_rate, snap_b.retry_rate),
            ("mean_tokens_per_task", snap_a.mean_tokens_per_task, snap_b.mean_tokens_per_task),
        ]:
            comparisons[metric] = self._compare_metric(
                metric, val_a, val_b, higher_is_better=False
            )

        # Aggregate score: weighted sum of improvements
        weights = {
            "completion_rate": 0.40,
            "confidence_mean": 0.30,
            "retry_rate": 0.15,
            "mean_tokens_per_task": 0.15,
        }

        aggregate_score = 0.0
        for metric, mc in comparisons.items():
            w = weights.get(metric, 0.25)
            if mc.direction == "improved":
                aggregate_score += w * mc.magnitude
            elif mc.direction == "degraded":
                aggregate_score -= w * mc.magnitude

        # Decision
        if aggregate_score > self._upgrade_threshold:
            recommendation = "upgrade"
            reason = "Version B shows statistically significant improvement."
        elif aggregate_score < -self._rollback_threshold:
            recommendation = "rollback"
            reason = "Version B shows significant degradation. Rolling back."
        else:
            recommendation = "hold"
            reason = "Differences not statistically significant."

        confidence = min(1.0, abs(aggregate_score) / max(self._upgrade_threshold, 0.001))

        return ComparisonResult(
            version_a_id=snap_a.run_id,
            version_b_id=snap_b.run_id,
            recommendation=recommendation,
            confidence=confidence,
            metric_comparisons=comparisons,
            reason=reason,
        )

    @staticmethod
    def _compare_metric(
        metric: str,
        val_a: float,
        val_b: float,
        higher_is_better: bool,
    ) -> MetricComparison:
        """Compare a single metric between A and B."""
        delta = val_b - val_a
        magnitude = abs(delta) / max(abs(val_a), 0.001)

        if abs(delta) < 0.001:
            direction = "unchanged"
        elif higher_is_better:
            direction = "improved" if delta > 0 else "degraded"
        else:
            direction = "improved" if delta < 0 else "degraded"

        return MetricComparison(
            metric=metric,
            value_a=val_a,
            value_b=val_b,
            delta=delta,
            direction=direction,
            magnitude=magnitude,
        )
