"""
veridian.hooks.builtin.behavioral_fingerprint
──────────────────────────────────────────────
BehavioralFingerprintHook — multi-dimensional per-run behavioral signature.

Computes a 7-dimensional fingerprint per run and compares it against the
previous run via cosine similarity. Alerts when divergence exceeds threshold.

Dimensions:
  1. action_distribution   — tool/verifier usage proportions
  2. output_structure      — output field distributions
  3. token_profile         — per-task token usage (normalized)
  4. verification_pattern  — retry count distribution
  5. tool_selection        — tool call sequence patterns
  6. latency_profile       — task processing latency (normalized)
  7. confidence_distribution — confidence score histogram

Read-only: never mutates ledger or task state.
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

__all__ = [
    "BehavioralFingerprintHook",
    "BehavioralFingerprint",
    "FingerprintReport",
]

log = logging.getLogger(__name__)


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class BehavioralFingerprint:
    """7-dimensional behavioral signature for a single run."""

    run_id: str = ""
    timestamp: str = ""
    dimensions: dict[str, float] = field(default_factory=dict)

    def cosine_similarity(self, other: BehavioralFingerprint) -> float:
        """Compute cosine similarity between this and another fingerprint."""
        keys = sorted(set(self.dimensions) | set(other.dimensions))
        a = [self.dimensions.get(k, 0.0) for k in keys]
        b = [other.dimensions.get(k, 0.0) for k in keys]

        dot = sum(x * y for x, y in zip(a, b, strict=True))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))

        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "dimensions": {k: round(v, 6) for k, v in self.dimensions.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BehavioralFingerprint:
        """Deserialize from dict."""
        return cls(
            run_id=d.get("run_id", ""),
            timestamp=d.get("timestamp", ""),
            dimensions=d.get("dimensions", {}),
        )


@dataclass
class FingerprintReport:
    """Fingerprint comparison report."""

    run_id: str = ""
    cosine_similarity: float = 1.0
    threshold: float = 0.85
    divergence_detected: bool = False
    dimensions_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "run_id": self.run_id,
            "cosine_similarity": round(self.cosine_similarity, 4),
            "threshold": self.threshold,
            "divergence_detected": self.divergence_detected,
            "dimensions_changed": self.dimensions_changed,
        }

    def to_markdown(self) -> str:
        """Generate fingerprint report markdown."""
        lines = [
            f"# Behavioral Fingerprint Report — {self.run_id}",
            "",
            f"**Cosine similarity:** {self.cosine_similarity:.4f}",
            f"**Threshold:** {self.threshold:.4f}",
            f"**Divergence detected:** {'YES' if self.divergence_detected else 'No'}",
            "",
        ]
        if self.dimensions_changed:
            lines.append("## Changed Dimensions")
            lines.append("")
            for dim in self.dimensions_changed:
                lines.append(f"- {dim}")
        lines.append("")
        return "\n".join(lines)


# ── Hook ─────────────────────────────────────────────────────────────────────


class BehavioralFingerprintHook(BaseHook):
    """Multi-dimensional behavioral signature per run.

    Computes 7 behavioral dimensions and compares against the previous run.
    Alerts on cosine similarity below threshold. Read-only.
    """

    id: ClassVar[str] = "behavioral_fingerprint"
    priority: ClassVar[int] = 88  # after evolution_monitor (85), before drift (90)

    def __init__(
        self,
        history_file: Path | str | None = None,
        similarity_threshold: float = 0.85,
        report_path: Path | str | None = None,
    ) -> None:
        if similarity_threshold < 0.0 or similarity_threshold > 1.0:
            raise VeridianConfigError(
                f"behavioral_fingerprint: similarity_threshold must be 0.0–1.0, "
                f"got {similarity_threshold}"
            )

        self._history_file = Path(history_file) if history_file else None
        self._similarity_threshold = similarity_threshold
        self._report_path = Path(report_path) if report_path else None

        # Per-run accumulators
        self._run_id: str = ""
        self._verifier_counts: dict[str, int] = {}
        self._tool_counts: dict[str, int] = {}
        self._token_usages: list[float] = []
        self._retry_counts: list[int] = []
        self._confidence_scores: list[float] = []
        self._output_field_counts: dict[str, int] = {}
        self._task_count: int = 0
        self._failed_count: int = 0

        # History and results
        self._history: list[BehavioralFingerprint] = []
        self.last_fingerprint: BehavioralFingerprint | None = None
        self.last_report: FingerprintReport | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def before_run(self, event: Any) -> None:
        """Reset accumulators and load history."""
        self._run_id = getattr(event, "run_id", "")
        self._verifier_counts = {}
        self._tool_counts = {}
        self._token_usages = []
        self._retry_counts = []
        self._confidence_scores = []
        self._output_field_counts = {}
        self._task_count = 0
        self._failed_count = 0
        self.last_fingerprint = None
        self.last_report = None
        self._history = self._load_history()

    def after_task(self, event: Any) -> None:
        """Accumulate behavioral metrics from completed task."""
        task = getattr(event, "task", None)
        result = getattr(event, "result", None)
        if task is None:
            return

        self._task_count += 1

        # Verifier distribution
        verifier_id = getattr(task, "verifier_id", "unknown")
        self._verifier_counts[verifier_id] = self._verifier_counts.get(verifier_id, 0) + 1

        # Retry count
        retry_count = getattr(task, "retry_count", 0)
        self._retry_counts.append(retry_count)

        if result is not None:
            # Token usage
            token_usage = getattr(result, "token_usage", {}) or {}
            total_tokens = token_usage.get("total_tokens", 0)
            self._token_usages.append(float(total_tokens))

            # Confidence
            confidence = getattr(result, "confidence", None)
            if confidence is not None:
                composite = getattr(confidence, "composite", None)
                if composite is not None:
                    self._confidence_scores.append(float(composite))

            # Tool calls
            tool_calls = getattr(result, "tool_calls", []) or []
            for tool in tool_calls:
                tool_name = str(tool)
                self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1

            # Output structure
            structured = getattr(result, "structured", {}) or {}
            if isinstance(structured, dict):
                for key in structured:
                    self._output_field_counts[key] = self._output_field_counts.get(key, 0) + 1

    def on_failure(self, event: Any) -> None:
        """Track failure count."""
        self._failed_count += 1

    def after_run(self, event: Any) -> None:
        """Compute fingerprint, compare, persist, report."""
        fp = self._compute_fingerprint()
        self.last_fingerprint = fp

        # Compare with previous fingerprint
        report = self._compare(fp)
        self.last_report = report

        if report.divergence_detected:
            log.warning(
                "fingerprint.divergence run_id=%s similarity=%.4f threshold=%.4f",
                fp.run_id,
                report.cosine_similarity,
                report.threshold,
            )

        self._persist_fingerprint(fp)

        if self._report_path is not None:
            self._write_report(report)

    # ── Fingerprint computation ──────────────────────────────────────────

    def _compute_fingerprint(self) -> BehavioralFingerprint:
        """Compute 7-dimensional behavioral fingerprint."""
        total = self._task_count + self._failed_count

        if total == 0:
            return BehavioralFingerprint(
                run_id=self._run_id,
                timestamp=datetime.now(tz=UTC).isoformat(),
                dimensions={
                    "action_distribution": 0.0,
                    "output_structure": 0.0,
                    "token_profile": 0.0,
                    "verification_pattern": 0.0,
                    "tool_selection": 0.0,
                    "latency_profile": 0.0,
                    "confidence_distribution": 0.0,
                },
            )

        # 1. Action distribution — entropy of verifier usage
        action_dist = self._normalized_entropy(self._verifier_counts)

        # 2. Output structure — entropy of output fields
        output_struct = self._normalized_entropy(self._output_field_counts)

        # 3. Token profile — normalized mean token usage
        token_profile = 0.0
        if self._token_usages:
            mean_tokens = sum(self._token_usages) / len(self._token_usages)
            # Normalize: sigmoid-like mapping, 1000 tokens ~ 0.5
            token_profile = min(1.0, mean_tokens / 2000.0)

        # 4. Verification pattern — retry rate
        verification_pattern = 0.0
        if self._retry_counts:
            mean_retries = sum(self._retry_counts) / len(self._retry_counts)
            verification_pattern = min(1.0, mean_retries / 3.0)  # 3 retries = 1.0

        # 5. Tool selection — entropy of tool usage
        tool_selection = self._normalized_entropy(self._tool_counts)

        # 6. Latency profile — success rate as proxy
        latency_profile = (total - self._failed_count) / total

        # 7. Confidence distribution — mean confidence
        confidence_dist = 0.0
        if self._confidence_scores:
            confidence_dist = sum(self._confidence_scores) / len(self._confidence_scores)

        return BehavioralFingerprint(
            run_id=self._run_id,
            timestamp=datetime.now(tz=UTC).isoformat(),
            dimensions={
                "action_distribution": action_dist,
                "output_structure": output_struct,
                "token_profile": token_profile,
                "verification_pattern": verification_pattern,
                "tool_selection": tool_selection,
                "latency_profile": latency_profile,
                "confidence_distribution": confidence_dist,
            },
        )

    @staticmethod
    def _normalized_entropy(counts: dict[str, int]) -> float:
        """Shannon entropy normalized to [0, 1]."""
        total = sum(counts.values())
        if total == 0:
            return 0.0
        n = len(counts)
        if n <= 1:
            return 0.0

        entropy = 0.0
        for count in counts.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)

        max_entropy = math.log2(n)
        return entropy / max_entropy if max_entropy > 0 else 0.0

    # ── Comparison ───────────────────────────────────────────────────────

    def _compare(self, current: BehavioralFingerprint) -> FingerprintReport:
        """Compare current fingerprint against previous."""
        if not self._history:
            return FingerprintReport(
                run_id=current.run_id,
                cosine_similarity=1.0,
                threshold=self._similarity_threshold,
                divergence_detected=False,
            )

        previous = self._history[-1]
        similarity = current.cosine_similarity(previous)

        # Find significantly changed dimensions
        changed: list[str] = []
        for dim in current.dimensions:
            curr_val = current.dimensions.get(dim, 0.0)
            prev_val = previous.dimensions.get(dim, 0.0)
            if abs(curr_val - prev_val) > 0.15:  # individual dim change threshold
                changed.append(dim)

        divergence = similarity < self._similarity_threshold

        return FingerprintReport(
            run_id=current.run_id,
            cosine_similarity=similarity,
            threshold=self._similarity_threshold,
            divergence_detected=divergence,
            dimensions_changed=changed,
        )

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_history(self) -> list[BehavioralFingerprint]:
        """Load JSONL history."""
        if self._history_file is None or not self._history_file.exists():
            return []
        fps: list[BehavioralFingerprint] = []
        for line in self._history_file.read_text().strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                fps.append(BehavioralFingerprint.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError):
                log.warning("behavioral_fingerprint: skipping corrupted history line")
        return fps

    def _persist_fingerprint(self, fp: BehavioralFingerprint) -> None:
        """Atomic write: append fingerprint to JSONL."""
        if self._history_file is None:
            return

        lines: list[str] = [json.dumps(f.to_dict()) for f in self._history]
        lines.append(json.dumps(fp.to_dict()))
        content = "\n".join(lines) + "\n"

        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._history_file.parent, delete=False, suffix=".tmp"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)
        os.replace(tmp_path, self._history_file)

    def _write_report(self, report: FingerprintReport) -> None:
        """Atomic write: fingerprint report."""
        if self._report_path is None:
            return
        content = report.to_markdown()
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._report_path.parent, delete=False, suffix=".tmp"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)
        os.replace(tmp_path, self._report_path)
