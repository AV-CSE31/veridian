"""
Tests for Problem 7: Pilot Failure — Drift + Fingerprint Detection.
Uses Veridian's real DriftDetectorHook and BehavioralFingerprint.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from solution import simulate_drift, simulate_fingerprint_divergence

from veridian.hooks.builtin.drift_detector import DriftDetectorHook, RunSnapshot
from veridian.hooks.builtin.behavioral_fingerprint import BehavioralFingerprint


class TestDetectsDegradation:
    """Prove silent drift (the 95% pilot failure pattern) is detectable."""

    def test_detects_completion_rate_drop(self) -> None:
        report = simulate_drift()
        assert report.overall_status != "stable"
        assert len(report.signals) >= 1

    def test_generates_actionable_recommendations(self) -> None:
        report = simulate_drift()
        assert len(report.recommended_actions) >= 1

    def test_signals_have_direction(self) -> None:
        report = simulate_drift()
        for s in report.signals:
            assert s.direction in ("degraded", "improved")

    def test_detects_fingerprint_divergence(self) -> None:
        similarity = simulate_fingerprint_divergence()
        assert similarity < 0.85


class TestStableRunsClean:
    """Prove stable agents are NOT falsely flagged."""

    def test_identical_fingerprints_similarity_one(self) -> None:
        fp = BehavioralFingerprint(run_id="r1", dimensions={"a": 0.5, "b": 0.8})
        assert fp.cosine_similarity(fp) == pytest.approx(1.0, abs=0.001)

    def test_similar_fingerprints_pass_threshold(self) -> None:
        fp1 = BehavioralFingerprint(run_id="r1", dimensions={"a": 0.80, "b": 0.70})
        fp2 = BehavioralFingerprint(run_id="r2", dimensions={"a": 0.82, "b": 0.69})
        assert fp1.cosine_similarity(fp2) > 0.95

    def test_stable_runs_no_drift(self) -> None:
        history = [
            RunSnapshot(run_id=f"r{i}", completion_rate=0.95, confidence_mean=0.88,
                        retry_rate=0.08, mean_tokens_per_task=450.0)
            for i in range(5)
        ]
        current = RunSnapshot(run_id="cur", completion_rate=0.94, confidence_mean=0.87,
                              retry_rate=0.09, mean_tokens_per_task=460.0)
        hook = DriftDetectorHook(window=5, threshold=0.15)
        report = hook._analyze_drift(current, history)
        assert report.overall_status == "stable"
