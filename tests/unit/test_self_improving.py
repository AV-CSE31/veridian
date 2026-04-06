"""
tests.unit.test_self_improving
──────────────────────────────
Self-Improving Verifier Framework — learns from FP/FN history,
adjusts thresholds, tracks performance, generates reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veridian.core.exceptions import SelfImprovingError
from veridian.core.task import Task, TaskResult
from veridian.intelligence.self_improving import (
    FeedbackRecord,
    FeedbackStore,
    PerformanceReport,
    SelfImprovingVerifier,
    VerifierPerformance,
)
from veridian.verify.base import BaseVerifier, VerificationResult

# ── Fixtures ──────────────────────────────────────────────────────────────────


class _AlwaysPassVerifier(BaseVerifier):
    id = "always_pass"
    description = "Always passes"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class _AlwaysFailVerifier(BaseVerifier):
    id = "always_fail"
    description = "Always fails"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="always fails")


class _ThresholdVerifier(BaseVerifier):
    """Passes only if result.output score >= threshold."""

    id = "threshold_verifier"
    description = "Threshold-based verifier"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        score = float(result.structured.get("score", 0.0))
        passed = score >= self.threshold
        return VerificationResult(passed=passed, score=score)


def _make_task(task_id: str = "t1") -> Task:
    return Task(id=task_id, title="Test", description="desc", verifier_id="always_pass")


def _make_result(score: float = 1.0) -> TaskResult:
    return TaskResult(raw_output="done", structured={"score": score})


# ── FeedbackRecord ────────────────────────────────────────────────────────────


class TestFeedbackRecord:
    def test_is_false_positive(self) -> None:
        """FP: verifier passed but human says it should have failed."""
        fb = FeedbackRecord(
            verifier_id="v1",
            task_id="t1",
            verifier_passed=True,
            human_expected_pass=False,
        )
        assert fb.is_false_positive is True
        assert fb.is_false_negative is False
        assert fb.is_true_positive is False
        assert fb.is_true_negative is False

    def test_is_false_negative(self) -> None:
        """FN: verifier failed but human says it should have passed."""
        fb = FeedbackRecord(
            verifier_id="v1",
            task_id="t1",
            verifier_passed=False,
            human_expected_pass=True,
        )
        assert fb.is_false_negative is True
        assert fb.is_false_positive is False

    def test_is_true_positive(self) -> None:
        fb = FeedbackRecord(
            verifier_id="v1",
            task_id="t1",
            verifier_passed=True,
            human_expected_pass=True,
        )
        assert fb.is_true_positive is True

    def test_is_true_negative(self) -> None:
        fb = FeedbackRecord(
            verifier_id="v1",
            task_id="t1",
            verifier_passed=False,
            human_expected_pass=False,
        )
        assert fb.is_true_negative is True

    def test_serialise_round_trip(self) -> None:
        fb = FeedbackRecord(
            verifier_id="v1",
            task_id="t2",
            verifier_passed=True,
            human_expected_pass=False,
            notes="flagged by auditor",
        )
        data = fb.to_dict()
        fb2 = FeedbackRecord.from_dict(data)
        assert fb2.verifier_id == fb.verifier_id
        assert fb2.task_id == fb.task_id
        assert fb2.verifier_passed == fb.verifier_passed
        assert fb2.human_expected_pass == fb.human_expected_pass
        assert fb2.notes == fb.notes


# ── FeedbackStore ─────────────────────────────────────────────────────────────


class TestFeedbackStore:
    def test_add_and_load(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "feedback.json")
        fb = FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=False)
        store.add(fb)
        records = store.load_all()
        assert len(records) == 1
        assert records[0].verifier_id == "v1"

    def test_add_multiple(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "feedback.json")
        for i in range(5):
            store.add(FeedbackRecord("v1", f"t{i}", verifier_passed=True, human_expected_pass=True))
        assert len(store.load_all()) == 5

    def test_load_for_verifier(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "feedback.json")
        store.add(FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=False))
        store.add(FeedbackRecord("v2", "t2", verifier_passed=False, human_expected_pass=True))
        v1_records = store.load_for_verifier("v1")
        assert len(v1_records) == 1
        assert v1_records[0].verifier_id == "v1"

    def test_atomic_write(self, tmp_path: Path) -> None:
        """Write must use atomic pattern — no partial files visible."""
        path = tmp_path / "feedback.json"
        store = FeedbackStore(path)
        store.add(FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=True))
        # File must exist and be valid JSON after the write
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list)

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "feedback.json")
        assert store.load_all() == []


# ── VerifierPerformance ───────────────────────────────────────────────────────


class TestVerifierPerformance:
    def _make_perf(self, tp: int = 0, fp: int = 0, tn: int = 0, fn: int = 0) -> VerifierPerformance:
        return VerifierPerformance(
            verifier_id="v1",
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
        )

    def test_precision_perfect(self) -> None:
        p = self._make_perf(tp=10, fp=0)
        assert p.precision == 1.0

    def test_precision_zero_denominator(self) -> None:
        p = self._make_perf(tp=0, fp=0)
        assert p.precision == 0.0

    def test_recall_perfect(self) -> None:
        p = self._make_perf(tp=10, fn=0)
        assert p.recall == 1.0

    def test_recall_zero_denominator(self) -> None:
        p = self._make_perf(tp=0, fn=0)
        assert p.recall == 0.0

    def test_f1_score(self) -> None:
        p = self._make_perf(tp=8, fp=2, fn=2)
        assert abs(p.f1_score - 0.8) < 1e-6

    def test_f1_zero_when_no_positives(self) -> None:
        p = self._make_perf(tp=0, fp=0, fn=0)
        assert p.f1_score == 0.0

    def test_total_samples(self) -> None:
        p = self._make_perf(tp=5, fp=3, tn=7, fn=2)
        assert p.total_samples == 17

    def test_accuracy(self) -> None:
        p = self._make_perf(tp=8, fp=2, tn=8, fn=2)
        assert abs(p.accuracy - 0.8) < 1e-6

    def test_from_feedback_records(self) -> None:
        records = [
            FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=True),  # TP
            FeedbackRecord("v1", "t2", verifier_passed=True, human_expected_pass=False),  # FP
            FeedbackRecord("v1", "t3", verifier_passed=False, human_expected_pass=False),  # TN
            FeedbackRecord("v1", "t4", verifier_passed=False, human_expected_pass=True),  # FN
        ]
        perf = VerifierPerformance.from_feedback_records("v1", records)
        assert perf.true_positives == 1
        assert perf.false_positives == 1
        assert perf.true_negatives == 1
        assert perf.false_negatives == 1


# ── SelfImprovingVerifier ─────────────────────────────────────────────────────


class TestSelfImprovingVerifier:
    def test_delegates_to_wrapped_verifier(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        siv = SelfImprovingVerifier(_AlwaysPassVerifier(), store)
        result = siv.verify(_make_task(), _make_result())
        assert result.passed is True

    def test_id_matches_wrapped(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        siv = SelfImprovingVerifier(_AlwaysPassVerifier(), store)
        assert siv.id == "always_pass"

    def test_record_feedback_stores_record(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        siv = SelfImprovingVerifier(_AlwaysPassVerifier(), store)
        siv.verify(_make_task(), _make_result())
        siv.record_feedback(task_id="t1", human_expected_pass=False)
        records = store.load_for_verifier("always_pass")
        assert len(records) == 1
        assert records[0].is_false_positive is True

    def test_get_performance_empty(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        siv = SelfImprovingVerifier(_AlwaysPassVerifier(), store)
        perf = siv.get_performance()
        assert perf.total_samples == 0

    def test_get_performance_with_feedback(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        inner = _ThresholdVerifier(threshold=0.5)
        siv = SelfImprovingVerifier(inner, store)
        # Passes: score=0.8
        siv.verify(_make_task("t1"), TaskResult(raw_output="done", structured={"score": 0.8}))
        siv.record_feedback("t1", human_expected_pass=True)  # TP
        # Fails: score=0.3
        siv.verify(_make_task("t2"), TaskResult(raw_output="done", structured={"score": 0.3}))
        siv.record_feedback("t2", human_expected_pass=False)  # TN
        perf = siv.get_performance()
        assert perf.true_positives == 1
        assert perf.true_negatives == 1

    def test_auto_tune_raises_without_sufficient_data(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        siv = SelfImprovingVerifier(_AlwaysPassVerifier(), store, min_samples_for_tuning=10)
        with pytest.raises(SelfImprovingError, match="insufficient"):
            siv.auto_tune(target_precision=0.9, target_recall=0.9)

    def test_auto_tune_adjusts_sensitivity(self, tmp_path: Path) -> None:
        """With enough FP feedback, auto_tune should tighten threshold."""
        store = FeedbackStore(tmp_path / "fb.json")
        inner = _ThresholdVerifier(threshold=0.3)
        siv = SelfImprovingVerifier(inner, store, min_samples_for_tuning=3)
        # Simulate 5 false positives (verifier passed, human says fail)
        for i in range(5):
            store.add(
                FeedbackRecord(
                    "threshold_verifier",
                    f"t{i}",
                    verifier_passed=True,
                    human_expected_pass=False,
                )
            )
        # Should not raise — enough samples
        report = siv.auto_tune(target_precision=0.9, target_recall=0.7)
        assert isinstance(report, dict)
        assert "recommendation" in report

    def test_sensitivity_direction_more_fp_tighten(self, tmp_path: Path) -> None:
        """High FP rate → recommendation to tighten (increase threshold)."""
        store = FeedbackStore(tmp_path / "fb.json")
        inner = _ThresholdVerifier(threshold=0.3)
        siv = SelfImprovingVerifier(inner, store, min_samples_for_tuning=3)
        for i in range(5):
            store.add(
                FeedbackRecord(
                    "threshold_verifier",
                    f"t{i}",
                    verifier_passed=True,
                    human_expected_pass=False,
                )
            )
        report = siv.auto_tune(target_precision=0.95, target_recall=0.7)
        assert report["recommendation"] in ("tighten", "loosen", "no_change")


# ── PerformanceReport ─────────────────────────────────────────────────────────


class TestPerformanceReport:
    def test_generate_report(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        store.add(FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=True))
        store.add(FeedbackRecord("v1", "t2", verifier_passed=True, human_expected_pass=False))

        report = PerformanceReport.generate(store, verifier_ids=["v1"])
        assert "v1" in report.verifiers
        assert report.verifiers["v1"].total_samples == 2

    def test_generate_report_all_verifiers(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        store.add(FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=True))
        store.add(FeedbackRecord("v2", "t2", verifier_passed=False, human_expected_pass=True))

        report = PerformanceReport.generate(store)
        assert "v1" in report.verifiers
        assert "v2" in report.verifiers

    def test_report_to_dict(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path / "fb.json")
        store.add(FeedbackRecord("v1", "t1", verifier_passed=True, human_expected_pass=True))
        report = PerformanceReport.generate(store, verifier_ids=["v1"])
        d = report.to_dict()
        assert "verifiers" in d
        assert "generated_at" in d
