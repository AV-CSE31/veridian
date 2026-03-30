"""
veridian.hooks.builtin.anomaly_detector
────────────────────────────────────────
AnomalyDetectorHook — mid-run behavioral anomaly detection.

Monitors intra-run behavior for:
  1. Token consumption spikes (context degradation signal)
  2. Tool usage anomalies (accessing tools never used before)
  3. Output pattern shifts (sudden change in structure/content)

Research basis: Agents of Chaos (Feb 2026) — shared environments corrupt
agents through incentive structures alone. This detector catches the
behavioral symptoms of environmental corruption.
"""

from __future__ import annotations

import enum
import logging
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = [
    "AnomalyDetectorHook",
    "AnomalyReport",
    "AnomalySignal",
    "AnomalyType",
]

log = logging.getLogger(__name__)


class AnomalyType(enum.Enum):
    """Types of behavioral anomalies."""

    TOKEN_SPIKE = "token_spike"
    TOOL_ANOMALY = "tool_anomaly"
    OUTPUT_SHIFT = "output_shift"


@dataclass
class AnomalySignal:
    """A single detected anomaly."""

    anomaly_type: AnomalyType = AnomalyType.TOKEN_SPIKE
    task_id: str = ""
    detail: str = ""
    severity: str = "warning"  # "warning" | "significant"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "anomaly_type": self.anomaly_type.value,
            "task_id": self.task_id,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass
class AnomalyReport:
    """Aggregated anomaly report for a single run."""

    run_id: str = ""
    signals: list[AnomalySignal] = field(default_factory=list)
    total_tasks_monitored: int = 0

    @property
    def is_clean(self) -> bool:
        """True if no anomalies detected."""
        return len(self.signals) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "run_id": self.run_id,
            "signals": [s.to_dict() for s in self.signals],
            "total_tasks_monitored": self.total_tasks_monitored,
            "is_clean": self.is_clean,
        }

    def to_markdown(self) -> str:
        """Generate anomaly report markdown."""
        lines = [
            f"# Anomaly Report — {self.run_id}",
            "",
            f"**Tasks monitored:** {self.total_tasks_monitored}",
            f"**Anomalies detected:** {len(self.signals)}",
            "",
        ]
        if self.is_clean:
            lines.append("No anomalies detected. Behavior within normal parameters.")
        else:
            lines.append("| Type | Task | Detail | Severity |")
            lines.append("|------|------|--------|----------|")
            for s in self.signals:
                lines.append(
                    f"| {s.anomaly_type.value} | {s.task_id} "
                    f"| {s.detail[:80]} | {s.severity} |"
                )
        lines.append("")
        return "\n".join(lines)


class AnomalyDetectorHook(BaseHook):
    """Mid-run behavioral anomaly detection.

    Builds an intra-run baseline from the first few tasks, then flags
    significant deviations in token usage, tool selection, and output structure.
    """

    id: ClassVar[str] = "anomaly_detector"
    priority: ClassVar[int] = 55  # runs in the middle band

    def __init__(
        self,
        spike_threshold: float = 3.0,
        baseline_window: int = 5,
    ) -> None:
        self._spike_threshold = spike_threshold
        self._baseline_window = baseline_window

        # Per-run accumulators
        self._run_id: str = ""
        self._token_usages: list[tuple[str, float]] = []  # (task_id, tokens)
        self._tool_sets: list[tuple[str, set[str]]] = []  # (task_id, tools)
        self._output_fields: list[tuple[str, set[str]]] = []  # (task_id, field_keys)
        self._signals: list[AnomalySignal] = []
        self._task_count: int = 0
        self.last_report: AnomalyReport | None = None

    def before_run(self, event: Any) -> None:
        """Reset accumulators."""
        self._run_id = getattr(event, "run_id", "")
        self._token_usages = []
        self._tool_sets = []
        self._output_fields = []
        self._signals = []
        self._task_count = 0
        self.last_report = None

    def after_task(self, event: Any) -> None:
        """Accumulate metrics and check for anomalies."""
        task = getattr(event, "task", None)
        result = getattr(event, "result", None)
        if task is None:
            return

        task_id = getattr(task, "id", f"t{self._task_count}")
        self._task_count += 1

        # Token usage
        if result is not None:
            token_usage = getattr(result, "token_usage", {}) or {}
            tokens = float(token_usage.get("total_tokens", 0))
            self._token_usages.append((task_id, tokens))
            self._check_token_spike(task_id, tokens)

            # Tool calls
            tool_calls = getattr(result, "tool_calls", []) or []
            tool_set = {str(t) for t in tool_calls}
            self._tool_sets.append((task_id, tool_set))
            self._check_tool_anomaly(task_id, tool_set)

            # Output structure
            structured = getattr(result, "structured", {}) or {}
            if isinstance(structured, dict):
                fields = set(structured.keys())
                self._output_fields.append((task_id, fields))
                self._check_output_shift(task_id, fields)

    def after_run(self, event: Any) -> None:
        """Build final report."""
        self.last_report = AnomalyReport(
            run_id=self._run_id,
            signals=list(self._signals),
            total_tasks_monitored=self._task_count,
        )
        if not self.last_report.is_clean:
            log.warning(
                "anomaly.detected run_id=%s anomalies=%d",
                self._run_id,
                len(self._signals),
            )

    def _check_token_spike(self, task_id: str, tokens: float) -> None:
        """Detect if current token usage spikes above baseline mean."""
        if len(self._token_usages) < self._baseline_window + 1:
            return  # still building baseline (need window + current)

        baseline = [t for _, t in self._token_usages[: self._baseline_window]]
        mean = sum(baseline) / len(baseline)
        std = math.sqrt(sum((t - mean) ** 2 for t in baseline) / len(baseline))

        # When std is 0 (all baseline identical), use mean as reference
        deviation = (tokens - mean) / max(std, mean * 0.1, 1.0)
        if deviation > self._spike_threshold:
            self._signals.append(
                AnomalySignal(
                    anomaly_type=AnomalyType.TOKEN_SPIKE,
                    task_id=task_id,
                    detail=f"Token usage {tokens:.0f} is {deviation:.1f}x "
                    f"above baseline mean {mean:.0f}",
                    severity="significant" if deviation > 5.0 else "warning",
                )
            )

    def _check_tool_anomaly(self, task_id: str, tools: set[str]) -> None:
        """Detect if new tools appear that weren't in the baseline."""
        if len(self._tool_sets) < self._baseline_window + 1 or not tools:
            return

        baseline_tools: set[str] = set()
        for _, ts in self._tool_sets[: self._baseline_window]:
            baseline_tools |= ts

        if not baseline_tools:
            return

        novel = tools - baseline_tools
        if novel:
            self._signals.append(
                AnomalySignal(
                    anomaly_type=AnomalyType.TOOL_ANOMALY,
                    task_id=task_id,
                    detail=f"Novel tools: {', '.join(sorted(novel))}",
                    severity="significant" if len(novel) >= 2 else "warning",
                )
            )

    def _check_output_shift(self, task_id: str, fields: set[str]) -> None:
        """Detect if output structure diverges from baseline."""
        if len(self._output_fields) < self._baseline_window + 1 or not fields:
            return

        # Compute baseline field set (union of all baseline outputs)
        baseline_fields: set[str] = set()
        for _, fs in self._output_fields[: self._baseline_window]:
            baseline_fields |= fs

        if not baseline_fields:
            return

        # Jaccard distance
        intersection = fields & baseline_fields
        union = fields | baseline_fields
        jaccard = len(intersection) / len(union) if union else 1.0

        if jaccard < 0.3:  # very different structure
            self._signals.append(
                AnomalySignal(
                    anomaly_type=AnomalyType.OUTPUT_SHIFT,
                    task_id=task_id,
                    detail=f"Output structure shift: Jaccard={jaccard:.2f}, "
                    f"new fields={fields - baseline_fields}",
                    severity="significant" if jaccard < 0.1 else "warning",
                )
            )
