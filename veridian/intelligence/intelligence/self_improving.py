"""
veridian.intelligence.self_improving
──────────────────────────────────────
Self-Improving Verifier Framework.

Verifiers that learn from their false positive/negative history via a
human-correction feedback loop.  Performance is tracked per verifier over
time and thresholds can be auto-tuned to meet precision/recall targets.

Design constraints (CLAUDE.md):
- All persistent state uses atomic temp-file + os.replace() writes.
- Dependency injection: FeedbackStore is injected, never instantiated inside.
- Raise from the hierarchy — only VeridianError subclasses.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veridian.core.exceptions import SelfImprovingError, VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK RECORD
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FeedbackRecord:
    """
    A single human-correction record for one verifier decision.

    verifier_passed      — what the verifier decided
    human_expected_pass  — what the human says the correct decision was
    """

    verifier_id: str
    task_id: str
    verifier_passed: bool
    human_expected_pass: bool
    notes: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def is_true_positive(self) -> bool:
        return self.verifier_passed and self.human_expected_pass

    @property
    def is_false_positive(self) -> bool:
        return self.verifier_passed and not self.human_expected_pass

    @property
    def is_true_negative(self) -> bool:
        return not self.verifier_passed and not self.human_expected_pass

    @property
    def is_false_negative(self) -> bool:
        return not self.verifier_passed and self.human_expected_pass

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "task_id": self.task_id,
            "verifier_passed": self.verifier_passed,
            "human_expected_pass": self.human_expected_pass,
            "notes": self.notes,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FeedbackRecord:
        return cls(
            verifier_id=d["verifier_id"],
            task_id=d["task_id"],
            verifier_passed=d["verifier_passed"],
            human_expected_pass=d["human_expected_pass"],
            notes=d.get("notes", ""),
            timestamp=d.get("timestamp", datetime.now(UTC).isoformat()),
        )


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK STORE  (atomic writes — CLAUDE.md §1.3)
# ─────────────────────────────────────────────────────────────────────────────


class FeedbackStore:
    """
    Append-only store of FeedbackRecord objects, persisted as a JSON array.

    Uses atomic temp-file + os.replace() to guarantee readers never see
    partial writes.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_all(self) -> list[FeedbackRecord]:
        if not self._path.exists():
            return []
        try:
            with open(self._path) as f:
                raw: list[dict[str, Any]] = json.load(f)
            return [FeedbackRecord.from_dict(d) for d in raw]
        except (json.JSONDecodeError, KeyError) as exc:
            log.warning("feedback_store.load_all parse_error path=%s err=%s", self._path, exc)
            return []

    def load_for_verifier(self, verifier_id: str) -> list[FeedbackRecord]:
        return [r for r in self.load_all() if r.verifier_id == verifier_id]

    # ── Write (atomic) ────────────────────────────────────────────────────────

    def add(self, record: FeedbackRecord) -> None:
        records = self.load_all()
        records.append(record)
        self._atomic_write([r.to_dict() for r in records])
        log.debug(
            "feedback_store.add verifier=%s task=%s fp=%s fn=%s",
            record.verifier_id,
            record.task_id,
            record.is_false_positive,
            record.is_false_negative,
        )

    def _atomic_write(self, data: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._path.parent, delete=False, suffix=".tmp"
        ) as f:
            json.dump(data, f, indent=2)
            tmp = Path(f.name)
        os.replace(tmp, self._path)


# ─────────────────────────────────────────────────────────────────────────────
# VERIFIER PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VerifierPerformance:
    """
    Confusion-matrix-based performance metrics for a single verifier.
    """

    verifier_id: str
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0

    # ── Metrics ───────────────────────────────────────────────────────────────

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1_score(self) -> float:
        denom = self.precision + self.recall
        return 2 * self.precision * self.recall / denom if denom > 0 else 0.0

    @property
    def accuracy(self) -> float:
        total = self.total_samples
        return (self.true_positives + self.true_negatives) / total if total > 0 else 0.0

    @property
    def total_samples(self) -> int:
        return (
            self.true_positives
            + self.false_positives
            + self.true_negatives
            + self.false_negatives
        )

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_feedback_records(
        cls, verifier_id: str, records: list[FeedbackRecord]
    ) -> VerifierPerformance:
        tp = sum(1 for r in records if r.is_true_positive)
        fp = sum(1 for r in records if r.is_false_positive)
        tn = sum(1 for r in records if r.is_true_negative)
        fn = sum(1 for r in records if r.is_false_negative)
        return cls(
            verifier_id=verifier_id,
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
            "accuracy": self.accuracy,
            "total_samples": self.total_samples,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SELF-IMPROVING VERIFIER
# ─────────────────────────────────────────────────────────────────────────────


class SelfImprovingVerifier(BaseVerifier):
    """
    Wraps any BaseVerifier and records each decision.  Human feedback is
    collected via ``record_feedback()`` and accumulated in the FeedbackStore.
    ``auto_tune()`` analyses accumulated feedback and returns a threshold
    recommendation.

    The wrapper is transparent — ``id`` and ``description`` delegate to the
    inner verifier.
    """

    # id is dynamically set per instance (see __init__)
    id = "_self_improving_placeholder"  # overridden below

    def __init__(
        self,
        inner: BaseVerifier,
        store: FeedbackStore,
        min_samples_for_tuning: int = 20,
    ) -> None:
        if min_samples_for_tuning < 1:
            raise VeridianConfigError("min_samples_for_tuning must be >= 1")
        self._inner = inner
        self._store = store
        self._min_samples = min_samples_for_tuning
        # Track most-recent result keyed by task_id for feedback correlation
        self._last_results: dict[str, bool] = {}

        # Override class-level id on instance — safe, does not mutate the class
        object.__setattr__(self, "id", inner.id)

    @property
    def description(self) -> str:  # type: ignore[override]
        return self._inner.description

    # ── Core verify ───────────────────────────────────────────────────────────

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        vr = self._inner.verify(task, result)
        self._last_results[task.id] = vr.passed
        log.debug("self_improving.verify id=%s task=%s passed=%s", self.id, task.id, vr.passed)
        return vr

    # ── Feedback recording ────────────────────────────────────────────────────

    def record_feedback(self, task_id: str, human_expected_pass: bool, notes: str = "") -> None:
        """
        Record human correction for a previously verified task.
        Must be called after verify() for the same task_id.
        """
        verifier_passed = self._last_results.get(task_id)
        if verifier_passed is None:
            log.warning(
                "self_improving.record_feedback no_prior_result task=%s verifier=%s",
                task_id,
                self.id,
            )
            # Still store the feedback — verifier_passed unknown, mark False
            verifier_passed = False

        record = FeedbackRecord(
            verifier_id=self.id,
            task_id=task_id,
            verifier_passed=verifier_passed,
            human_expected_pass=human_expected_pass,
            notes=notes,
        )
        self._store.add(record)

    # ── Performance query ─────────────────────────────────────────────────────

    def get_performance(self) -> VerifierPerformance:
        """Return current confusion-matrix metrics from all stored feedback."""
        records = self._store.load_for_verifier(self.id)
        return VerifierPerformance.from_feedback_records(self.id, records)

    # ── Auto-tuning ───────────────────────────────────────────────────────────

    def auto_tune(
        self,
        target_precision: float = 0.9,
        target_recall: float = 0.9,
    ) -> dict[str, Any]:
        """
        Analyse feedback history and return a threshold recommendation.

        Raises SelfImprovingError if there is insufficient data.

        Returns a dict with keys:
          - current_precision, current_recall, current_f1
          - recommendation: "tighten" | "loosen" | "no_change"
          - reason: human-readable explanation
        """
        records = self._store.load_for_verifier(self.id)
        if len(records) < self._min_samples:
            raise SelfImprovingError(
                f"auto_tune requires at least {self._min_samples} feedback samples "
                f"(insufficient: {len(records)} available for verifier '{self.id}')"
            )

        perf = VerifierPerformance.from_feedback_records(self.id, records)

        # Determine recommendation
        if perf.precision < target_precision and perf.recall >= target_recall:
            recommendation = "tighten"
            reason = (
                f"Precision {perf.precision:.2f} < target {target_precision:.2f}. "
                "Too many false positives — tighten the threshold."
            )
        elif perf.recall < target_recall and perf.precision >= target_precision:
            recommendation = "loosen"
            reason = (
                f"Recall {perf.recall:.2f} < target {target_recall:.2f}. "
                "Too many false negatives — loosen the threshold."
            )
        elif perf.precision < target_precision and perf.recall < target_recall:
            # Both below target — prioritise whichever is further from target
            precision_gap = target_precision - perf.precision
            recall_gap = target_recall - perf.recall
            recommendation = "tighten" if precision_gap > recall_gap else "loosen"
            reason = (
                f"Both precision ({perf.precision:.2f}) and recall ({perf.recall:.2f}) "
                "below targets. Prioritising larger gap."
            )
        else:
            recommendation = "no_change"
            reason = (
                f"Precision {perf.precision:.2f} ≥ {target_precision:.2f} and "
                f"recall {perf.recall:.2f} ≥ {target_recall:.2f}. No adjustment needed."
            )

        log.info(
            "self_improving.auto_tune verifier=%s recommendation=%s precision=%.2f recall=%.2f",
            self.id,
            recommendation,
            perf.precision,
            perf.recall,
        )

        return {
            "verifier_id": self.id,
            "current_precision": perf.precision,
            "current_recall": perf.recall,
            "current_f1": perf.f1_score,
            "target_precision": target_precision,
            "target_recall": target_recall,
            "recommendation": recommendation,
            "reason": reason,
            "samples_analysed": len(records),
        }


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PerformanceReport:
    """
    Aggregated performance report across one or more verifiers.
    """

    verifiers: dict[str, VerifierPerformance]
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @classmethod
    def generate(
        cls,
        store: FeedbackStore,
        verifier_ids: list[str] | None = None,
    ) -> PerformanceReport:
        """
        Generate a performance report from the feedback store.

        If verifier_ids is None, include all verifiers found in the store.
        """
        all_records = store.load_all()

        if verifier_ids is None:
            verifier_ids = sorted({r.verifier_id for r in all_records})

        perfs: dict[str, VerifierPerformance] = {}
        for vid in verifier_ids:
            recs = [r for r in all_records if r.verifier_id == vid]
            perfs[vid] = VerifierPerformance.from_feedback_records(vid, recs)

        return cls(verifiers=perfs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "verifiers": {vid: perf.to_dict() for vid, perf in self.verifiers.items()},
        }
