"""
veridian.hooks.builtin.drift_detector
─────────────────────────────────────
DriftDetectorHook — behavioral regression detection across runs.

Collects per-run metrics (pass/fail rates, confidence distribution, token usage,
retry rates), persists them as JSONL snapshots, and compares the current run
against a configurable historical window using z-score and Bayesian methods.

Read-only: never mutates the ledger or any external state beyond its own
history file and optional report file.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.hooks.base import BaseHook

__all__ = ["DriftDetectorHook", "RunSnapshot", "DriftSignal", "DriftReport"]

log = logging.getLogger(__name__)


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class RunSnapshot:
    """Metrics captured at the end of a single run for drift comparison."""

    run_id: str = ""
    timestamp: str = ""
    total_tasks: int = 0
    done_count: int = 0
    failed_count: int = 0
    abandoned_count: int = 0
    verifier_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    confidence_mean: float = 0.0
    confidence_std: float = 0.0
    confidence_tier_counts: dict[str, int] = field(default_factory=dict)
    retry_rate: float = 0.0
    mean_tokens_per_task: float = 0.0
    completion_rate: float = 0.0
    failure_modes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_tasks": self.total_tasks,
            "done_count": self.done_count,
            "failed_count": self.failed_count,
            "abandoned_count": self.abandoned_count,
            "verifier_stats": self.verifier_stats,
            "confidence_mean": self.confidence_mean,
            "confidence_std": self.confidence_std,
            "confidence_tier_counts": self.confidence_tier_counts,
            "retry_rate": self.retry_rate,
            "mean_tokens_per_task": self.mean_tokens_per_task,
            "completion_rate": self.completion_rate,
            "failure_modes": self.failure_modes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunSnapshot:
        """Deserialize from dict with safe defaults for missing fields."""
        return cls(
            run_id=d.get("run_id", ""),
            timestamp=d.get("timestamp", ""),
            total_tasks=d.get("total_tasks", 0),
            done_count=d.get("done_count", 0),
            failed_count=d.get("failed_count", 0),
            abandoned_count=d.get("abandoned_count", 0),
            verifier_stats=d.get("verifier_stats", {}),
            confidence_mean=d.get("confidence_mean", 0.0),
            confidence_std=d.get("confidence_std", 0.0),
            confidence_tier_counts=d.get("confidence_tier_counts", {}),
            retry_rate=d.get("retry_rate", 0.0),
            mean_tokens_per_task=d.get("mean_tokens_per_task", 0.0),
            completion_rate=d.get("completion_rate", 0.0),
            failure_modes=d.get("failure_modes", {}),
        )


@dataclass
class DriftSignal:
    """A single metric that has drifted beyond threshold."""

    metric: str = ""
    baseline_mean: float = 0.0
    baseline_std: float = 0.0
    current_value: float = 0.0
    z_score: float = 0.0
    magnitude: float = 0.0
    direction: str = ""  # "degraded" | "improved"
    significance: str = ""  # "significant" | "warning" | "normal"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "metric": self.metric,
            "baseline_mean": round(self.baseline_mean, 4),
            "baseline_std": round(self.baseline_std, 4),
            "current_value": round(self.current_value, 4),
            "z_score": round(self.z_score, 2),
            "magnitude": round(self.magnitude, 4),
            "direction": self.direction,
            "significance": self.significance,
        }


@dataclass
class DriftReport:
    """Aggregated drift analysis for a single run."""

    run_id: str = ""
    timestamp: str = ""
    window_size: int = 0
    signals: list[DriftSignal] = field(default_factory=list)
    overall_status: str = "stable"  # "stable" | "warning" | "drifting"
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "window_size": self.window_size,
            "signals": [s.to_dict() for s in self.signals],
            "overall_status": self.overall_status,
            "recommended_actions": self.recommended_actions,
        }

    def to_markdown(self) -> str:
        """Generate drift_report.md content."""
        lines = [
            f"# Drift Report — {self.run_id}",
            "",
            f"**Timestamp:** {self.timestamp}",
            f"**Window size:** {self.window_size} runs",
            f"**Overall status:** {self.overall_status.upper()}",
            "",
        ]
        if not self.signals:
            lines.append("No drift signals detected. Agent behavior is stable.")
        else:
            lines.append("## Signals")
            lines.append("")
            lines.append("| Metric | Baseline | Current | Z-Score | Direction | Significance |")
            lines.append("|--------|----------|---------|---------|-----------|--------------|")
            for s in self.signals:
                lines.append(
                    f"| {s.metric} | {s.baseline_mean:.4f} "
                    f"| {s.current_value:.4f} | {s.z_score:.2f} "
                    f"| {s.direction} | {s.significance} |"
                )
            lines.append("")

        if self.recommended_actions:
            lines.append("## Recommended Actions")
            lines.append("")
            for action in self.recommended_actions:
                lines.append(f"- {action}")
            lines.append("")

        return "\n".join(lines)


# ── Hook ─────────────────────────────────────────────────────────────────────


class DriftDetectorHook(BaseHook):
    """Behavioral regression detection across runs.

    Collects per-run metrics, persists snapshots to JSONL, compares current
    run against historical baseline, flags statistically significant drift.
    Read-only: never mutates ledger or task state.
    """

    id: ClassVar[str] = "drift_detector"
    priority: ClassVar[int] = 90  # runs late — needs all metrics collected

    def __init__(
        self,
        history_file: Path | str | None = None,
        window: int = 10,
        threshold: float = 0.15,
        z_threshold: float = 2.0,
        report_path: Path | str | None = None,
    ) -> None:
        if window < 1:
            raise VeridianConfigError(f"drift_detector: window must be >= 1, got {window}")
        if threshold < 0.0 or threshold > 1.0:
            raise VeridianConfigError(f"drift_detector: threshold must be 0.0–1.0, got {threshold}")

        self._history_file = Path(history_file) if history_file else None
        self._window = window
        self._threshold = threshold
        self._z_threshold = z_threshold
        self._report_path = Path(report_path) if report_path else None

        # Per-run accumulators (reset on before_run)
        self._run_id: str = ""
        self._total_tasks: int = 0
        self._verifier_pass_counts: dict[str, int] = {}
        self._verifier_fail_counts: dict[str, int] = {}
        self._confidence_scores: list[float] = []
        self._total_tokens: int = 0
        self._task_count: int = 0
        self._total_retries: int = 0
        self._failure_errors: list[str] = []

        # Historical data
        self._history: list[RunSnapshot] = []

        # Last report (accessible for testing and runner integration)
        self.last_report: DriftReport | None = None

    # ── Lifecycle methods ────────────────────────────────────────────────

    def before_run(self, event: Any) -> None:
        """Initialize accumulators and load history."""
        self._run_id = getattr(event, "run_id", "")
        self._total_tasks = getattr(event, "total_tasks", 0)
        self._verifier_pass_counts = {}
        self._verifier_fail_counts = {}
        self._confidence_scores = []
        self._total_tokens = 0
        self._task_count = 0
        self._total_retries = 0
        self._failure_errors = []
        self.last_report = None
        self._history = self._load_history()

    def after_task(self, event: Any) -> None:
        """Accumulate metrics from completed task."""
        task = getattr(event, "task", None)
        result = getattr(event, "result", None)
        if task is None:
            return

        verifier_id = getattr(task, "verifier_id", "unknown")
        self._verifier_pass_counts[verifier_id] = self._verifier_pass_counts.get(verifier_id, 0) + 1

        retry_count = getattr(task, "retry_count", 0)
        self._total_retries += retry_count
        self._task_count += 1

        if result is not None:
            token_usage = getattr(result, "token_usage", {})
            self._total_tokens += token_usage.get("total_tokens", 0)

            confidence = getattr(result, "confidence", None)
            if confidence is not None:
                composite = None
                if isinstance(confidence, dict):
                    composite = confidence.get("composite")
                elif isinstance(confidence, (int, float)):
                    composite = float(confidence)
                else:
                    composite = getattr(confidence, "composite", None)
                if composite is not None:
                    self._confidence_scores.append(float(composite))

    def on_failure(self, event: Any) -> None:
        """Accumulate failure metrics."""
        task = getattr(event, "task", None)
        if task is not None:
            verifier_id = getattr(task, "verifier_id", "unknown")
            self._verifier_fail_counts[verifier_id] = (
                self._verifier_fail_counts.get(verifier_id, 0) + 1
            )

        error = getattr(event, "error", "")
        if error:
            # Normalize to first 80 chars for clustering
            self._failure_errors.append(str(error)[:80])

    def after_run(self, event: Any) -> None:
        """Build snapshot, persist, analyze drift, generate report."""
        summary = getattr(event, "summary", None)
        snapshot = self._build_snapshot(summary)
        self._persist_snapshot(snapshot)
        report = self._analyze_drift(snapshot, self._history)
        self.last_report = report

        if report.overall_status != "stable":
            log.warning(
                "drift.detected run_id=%s status=%s signals=%d",
                report.run_id,
                report.overall_status,
                len(report.signals),
            )
            for signal in report.signals:
                log.warning(
                    "drift.signal metric=%s baseline=%.4f current=%.4f z=%.2f direction=%s",
                    signal.metric,
                    signal.baseline_mean,
                    signal.current_value,
                    signal.z_score,
                    signal.direction,
                )

        if self._report_path is not None:
            self._write_report(report)

    # ── Internal methods ─────────────────────────────────────────────────

    def _build_snapshot(self, summary: Any) -> RunSnapshot:
        """Aggregate accumulators into a RunSnapshot."""
        done = getattr(summary, "done_count", 0) if summary else 0
        failed = getattr(summary, "failed_count", 0) if summary else 0
        abandoned = getattr(summary, "abandoned_count", 0) if summary else 0
        total = getattr(summary, "total_tasks", self._total_tasks) if summary else self._total_tasks

        # Build verifier stats
        all_verifiers = set(self._verifier_pass_counts) | set(self._verifier_fail_counts)
        verifier_stats: dict[str, dict[str, int]] = {}
        for vid in all_verifiers:
            verifier_stats[vid] = {
                "pass": self._verifier_pass_counts.get(vid, 0),
                "fail": self._verifier_fail_counts.get(vid, 0),
            }

        # Confidence stats
        conf_mean = 0.0
        conf_std = 0.0
        if self._confidence_scores:
            conf_mean = sum(self._confidence_scores) / len(self._confidence_scores)
            if len(self._confidence_scores) > 1:
                variance = sum((c - conf_mean) ** 2 for c in self._confidence_scores) / len(
                    self._confidence_scores
                )
                conf_std = math.sqrt(variance)

        # Confidence tier counts
        tier_counts: dict[str, int] = {
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "UNCERTAIN": 0,
        }
        for score in self._confidence_scores:
            if score >= 0.85:
                tier_counts["HIGH"] += 1
            elif score >= 0.65:
                tier_counts["MEDIUM"] += 1
            elif score >= 0.40:
                tier_counts["LOW"] += 1
            else:
                tier_counts["UNCERTAIN"] += 1

        # Retry rate
        total_processed = self._task_count + failed
        retry_rate = self._total_retries / max(1, total_processed)

        # Mean tokens
        mean_tokens = self._total_tokens / max(1, self._task_count)

        # Completion rate
        completion_rate = done / max(1, total)

        # Failure modes
        failure_modes: dict[str, int] = {}
        for err in self._failure_errors:
            failure_modes[err] = failure_modes.get(err, 0) + 1

        return RunSnapshot(
            run_id=self._run_id,
            timestamp=datetime.now(tz=UTC).isoformat(),
            total_tasks=total,
            done_count=done,
            failed_count=failed,
            abandoned_count=abandoned,
            verifier_stats=verifier_stats,
            confidence_mean=conf_mean,
            confidence_std=conf_std,
            confidence_tier_counts=tier_counts,
            retry_rate=retry_rate,
            mean_tokens_per_task=mean_tokens,
            completion_rate=completion_rate,
            failure_modes=failure_modes,
        )

    def _load_history(self) -> list[RunSnapshot]:
        """Read JSONL file, parse each line into RunSnapshot. Skip bad lines."""
        if self._history_file is None or not self._history_file.exists():
            return []

        snapshots: list[RunSnapshot] = []
        for line in self._history_file.read_text().strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                snapshots.append(RunSnapshot.from_dict(d))
            except (json.JSONDecodeError, KeyError, TypeError):
                log.warning("drift_detector: skipping corrupted history line")
                continue
        return snapshots

    def _persist_snapshot(self, snapshot: RunSnapshot) -> None:
        """Atomic write: append snapshot to history file via os.replace()."""
        if self._history_file is None:
            return

        # Build full content: existing history + new snapshot
        lines: list[str] = []
        for snap in self._history:
            lines.append(json.dumps(snap.to_dict()))
        lines.append(json.dumps(snapshot.to_dict()))
        content = "\n".join(lines) + "\n"

        # Atomic write
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=self._history_file.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        os.replace(tmp_path, self._history_file)

    def _analyze_drift(self, current: RunSnapshot, history: list[RunSnapshot]) -> DriftReport:
        """Compare current snapshot against the last N runs in the window."""
        report = DriftReport(
            run_id=current.run_id,
            timestamp=current.timestamp,
            window_size=min(len(history), self._window),
        )

        # Need at least 2 historical runs for meaningful stats
        if len(history) < 2:
            report.overall_status = "stable"
            return report

        window = history[-self._window :]
        signals: list[DriftSignal] = []

        # 1. Per-verifier pass rate drift (Bayesian)
        for vid, stats in current.verifier_stats.items():
            current_passes = stats.get("pass", 0)
            current_fails = stats.get("fail", 0)
            current_total = current_passes + current_fails
            if current_total == 0:
                continue

            baseline_passes = 0
            baseline_fails = 0
            for snap in window:
                vs = snap.verifier_stats.get(vid, {})
                baseline_passes += vs.get("pass", 0)
                baseline_fails += vs.get("fail", 0)

            baseline_total = baseline_passes + baseline_fails
            if baseline_total == 0:
                continue

            baseline_rate = baseline_passes / baseline_total
            current_rate = current_passes / current_total

            # Bayesian lower bound (same formula as skills/models.py)
            alpha = baseline_passes + 1
            beta_ = baseline_fails + 1
            n = alpha + beta_
            p = alpha / n
            variance = (p * (1.0 - p)) / n
            lower_bound = max(0.0, p - 1.96 * math.sqrt(variance))

            delta = current_rate - baseline_rate
            magnitude = abs(delta) / max(baseline_rate, 0.001)

            if current_rate < lower_bound and magnitude > self._threshold:
                # Compute z-score for reporting
                baseline_std = math.sqrt(
                    sum(
                        (
                            (
                                snap.verifier_stats.get(vid, {}).get("pass", 0)
                                / max(
                                    1,
                                    snap.verifier_stats.get(vid, {}).get("pass", 0)
                                    + snap.verifier_stats.get(vid, {}).get("fail", 0),
                                )
                            )
                            - baseline_rate
                        )
                        ** 2
                        for snap in window
                    )
                    / max(1, len(window))
                )
                z = (current_rate - baseline_rate) / max(baseline_std, 0.001)

                signals.append(
                    DriftSignal(
                        metric=f"verification_pass_rate.{vid}",
                        baseline_mean=baseline_rate,
                        baseline_std=baseline_std,
                        current_value=current_rate,
                        z_score=z,
                        magnitude=magnitude,
                        direction="degraded",
                        significance="significant",
                    )
                )

        # 2. Confidence mean drift (z-score)
        conf_signal = self._check_z_score_drift(
            metric="confidence_mean",
            values=[s.confidence_mean for s in window],
            current=current.confidence_mean,
            lower_is_worse=True,
        )
        if conf_signal is not None:
            signals.append(conf_signal)

        # 3. Retry rate drift (z-score, higher is worse)
        retry_signal = self._check_z_score_drift(
            metric="retry_rate",
            values=[s.retry_rate for s in window],
            current=current.retry_rate,
            lower_is_worse=False,
        )
        if retry_signal is not None:
            signals.append(retry_signal)

        # 4. Token consumption drift (z-score, higher is worse)
        token_signal = self._check_z_score_drift(
            metric="mean_tokens_per_task",
            values=[s.mean_tokens_per_task for s in window],
            current=current.mean_tokens_per_task,
            lower_is_worse=False,
        )
        if token_signal is not None:
            signals.append(token_signal)

        # 5. Failure mode clustering (only meaningful with 3+ failures)
        if current.failure_modes:
            total_failures = sum(current.failure_modes.values())
            if total_failures >= 3:
                dominant_mode = max(
                    current.failure_modes,
                    key=current.failure_modes.get,  # type: ignore[arg-type]
                )
                dominant_pct = current.failure_modes[dominant_mode] / total_failures
                if dominant_pct > 0.5:
                    # Check if this mode was dominant in baseline
                    baseline_dominant = False
                    for snap in window:
                        if snap.failure_modes:
                            bt = sum(snap.failure_modes.values())
                            if bt > 0:
                                bmax = max(snap.failure_modes.values())
                                if bmax / bt > 0.5:
                                    baseline_dominant = True
                    if not baseline_dominant:
                        signals.append(
                            DriftSignal(
                                metric=f"failure_mode.{dominant_mode[:40]}",
                                baseline_mean=0.0,
                                baseline_std=0.0,
                                current_value=dominant_pct,
                                z_score=0.0,
                                magnitude=dominant_pct,
                                direction="degraded",
                                significance="warning",
                            )
                        )

        report.signals = signals

        # Only count degradation signals for overall status
        degraded_significant = sum(
            1 for s in signals if s.significance == "significant" and s.direction == "degraded"
        )
        degraded_warning = sum(
            1 for s in signals if s.significance == "warning" and s.direction == "degraded"
        )

        if degraded_significant >= 2 or (degraded_significant >= 1 and degraded_warning >= 1):
            report.overall_status = "drifting"
        elif degraded_significant >= 1 or degraded_warning >= 1:
            report.overall_status = "warning"
        else:
            report.overall_status = "stable"

        # Generate recommended actions
        report.recommended_actions = self._generate_actions(signals)

        return report

    def _check_z_score_drift(
        self,
        metric: str,
        values: list[float],
        current: float,
        lower_is_worse: bool,
    ) -> DriftSignal | None:
        """Check if current value deviates significantly from historical values."""
        if not values:
            return None

        mean = sum(values) / len(values)
        if len(values) < 2:
            return None

        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance)
        z = (current - mean) / max(std, 0.001)

        delta = abs(current - mean)
        magnitude = delta / max(abs(mean), 0.001)

        if magnitude < self._threshold:
            return None

        # Determine direction
        is_degraded = current < mean if lower_is_worse else current > mean

        direction = "degraded" if is_degraded else "improved"

        # Determine significance
        abs_z = abs(z)
        if abs_z > self._z_threshold and magnitude > self._threshold:
            significance = "significant"
        elif abs_z > self._z_threshold * 0.75 and magnitude > self._threshold * 0.5:
            significance = "warning"
        else:
            return None

        return DriftSignal(
            metric=metric,
            baseline_mean=mean,
            baseline_std=std,
            current_value=current,
            z_score=z,
            magnitude=magnitude,
            direction=direction,
            significance=significance,
        )

    def _generate_actions(self, signals: list[DriftSignal]) -> list[str]:
        """Generate actionable recommendations based on drift signals."""
        actions: list[str] = []
        for signal in signals:
            if "pass_rate" in signal.metric:
                vid = signal.metric.split(".")[-1]
                actions.append(
                    f"Review {vid} verifier: pass rate dropped from "
                    f"{signal.baseline_mean:.0%} to {signal.current_value:.0%}. "
                    f"Check if model or prompt changed."
                )
            elif "confidence" in signal.metric:
                actions.append(
                    "Confidence scores degraded. Consider checking model version, "
                    "temperature settings, or prompt quality."
                )
            elif "retry" in signal.metric:
                actions.append(
                    "Retry rate increased. Tasks are taking more attempts to complete. "
                    "Review error patterns and verifier configurations."
                )
            elif "token" in signal.metric:
                actions.append(
                    "Token consumption increased. Possible context degradation. "
                    "Review context window settings and compaction threshold."
                )
            elif "failure_mode" in signal.metric:
                mode = signal.metric.replace("failure_mode.", "")
                actions.append(
                    f"Dominant failure mode detected: '{mode}'. "
                    f"This error accounts for {signal.current_value:.0%} of failures."
                )
        return actions

    def _write_report(self, report: DriftReport) -> None:
        """Write drift_report.md via atomic write."""
        if self._report_path is None:
            return

        content = report.to_markdown()
        self._report_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            "w",
            dir=self._report_path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        os.replace(tmp_path, self._report_path)
