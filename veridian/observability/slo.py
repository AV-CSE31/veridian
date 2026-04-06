"""
veridian.observability.slo
──────────────────────────
SLO definitions, evaluation engine, and built-in SLO catalogue.

Rules:
- SLOComparison defines comparison operators for SLO targets.
- SLODefinition is a frozen specification; SLOReport is the evaluation output.
- SLOEvaluator skips metrics not present in the input dict (no KeyError).
- BUILTIN_SLOS provides sensible defaults for common agent metrics.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)

__all__ = [
    "BUILTIN_SLOS",
    "SLOComparison",
    "SLODefinition",
    "SLOEvaluator",
    "SLOReport",
]


# ── Enums ────────────────────────────────────────────────────────────────────


class SLOComparison(enum.Enum):
    """Comparison operator for SLO target evaluation."""

    LESS_THAN = "less_than"
    GREATER_THAN = "greater_than"
    EQUAL = "equal"


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SLODefinition:
    """Immutable specification for a Service Level Objective."""

    name: str
    metric_name: str
    target_value: float
    window_seconds: int
    comparison: SLOComparison
    description: str


@dataclass(frozen=True)
class SLOReport:
    """Result of evaluating a single SLO against current metrics."""

    slo_name: str
    current_value: float
    target: float
    in_compliance: bool
    window_seconds: int
    evaluated_at: str


# ── Evaluator ────────────────────────────────────────────────────────────────


class SLOEvaluator:
    """Evaluate a set of SLO definitions against current metric values.

    Usage::

        evaluator = SLOEvaluator(definitions=BUILTIN_SLOS)
        reports = evaluator.evaluate({"failure_rate": 0.02, "task_latency_p99": 12.0})
    """

    def __init__(self, definitions: list[SLODefinition]) -> None:
        self._definitions = list(definitions)

    def evaluate(self, metrics: dict[str, float]) -> list[SLOReport]:
        """Evaluate all SLO definitions against the provided metrics.

        Metrics not present in *metrics* are silently skipped (no report emitted).

        Returns
        -------
        list[SLOReport]
            One report per SLO whose metric_name exists in *metrics*.
        """
        now_iso = datetime.now(tz=UTC).isoformat()
        reports: list[SLOReport] = []

        for defn in self._definitions:
            if defn.metric_name not in metrics:
                log.debug("SLO %s: metric %s not in input, skipping", defn.name, defn.metric_name)
                continue

            current = metrics[defn.metric_name]
            in_compliance = self._check_compliance(current, defn.target_value, defn.comparison)

            report = SLOReport(
                slo_name=defn.name,
                current_value=current,
                target=defn.target_value,
                in_compliance=in_compliance,
                window_seconds=defn.window_seconds,
                evaluated_at=now_iso,
            )
            reports.append(report)

            if not in_compliance:
                log.warning(
                    "SLO violation: %s — current=%s target=%s (%s)",
                    defn.name,
                    current,
                    defn.target_value,
                    defn.comparison.value,
                )

        return reports

    @staticmethod
    def _check_compliance(current: float, target: float, comparison: SLOComparison) -> bool:
        """Return True if *current* satisfies the comparison against *target*."""
        if comparison == SLOComparison.LESS_THAN:
            return current < target
        if comparison == SLOComparison.GREATER_THAN:
            return current > target
        # EQUAL
        return current == target


# ── Built-in SLO catalogue ──────────────────────────────────────────────────

BUILTIN_SLOS: list[SLODefinition] = [
    SLODefinition(
        name="task_latency_p99",
        metric_name="task_latency_p99",
        target_value=30.0,
        window_seconds=3600,
        comparison=SLOComparison.LESS_THAN,
        description="P99 task latency must be under 30 seconds",
    ),
    SLODefinition(
        name="failure_rate",
        metric_name="failure_rate",
        target_value=0.05,
        window_seconds=3600,
        comparison=SLOComparison.LESS_THAN,
        description="Task failure rate must be under 5%",
    ),
    SLODefinition(
        name="retry_rate",
        metric_name="retry_rate",
        target_value=0.2,
        window_seconds=3600,
        comparison=SLOComparison.LESS_THAN,
        description="Task retry rate must be under 20%",
    ),
    SLODefinition(
        name="cost_per_task",
        metric_name="cost_per_task",
        target_value=1.0,
        window_seconds=3600,
        comparison=SLOComparison.LESS_THAN,
        description="Average cost per task must be under $1.00",
    ),
    SLODefinition(
        name="approval_lag",
        metric_name="approval_lag",
        target_value=300.0,
        window_seconds=3600,
        comparison=SLOComparison.LESS_THAN,
        description="Human approval lag must be under 300 seconds",
    ),
]
