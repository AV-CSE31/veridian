"""
Tests for veridian.hooks.builtin.behavioral_fingerprint
───────────────────────────────────────────────────────
Multi-dimensional behavioral signature computation and divergence detection.
TDD: RED phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.hooks.builtin.behavioral_fingerprint import (
    BehavioralFingerprint,
    BehavioralFingerprintHook,
    FingerprintReport,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeRunStarted:
    run_id: str = "run-001"
    total_tasks: int = 10


@dataclass
class _FakeTask:
    id: str = "t1"
    verifier_id: str = "schema"
    retry_count: int = 0
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class _FakeConfidence:
    composite: float = 0.85


@dataclass
class _FakeResult:
    structured: dict[str, Any] = None  # type: ignore[assignment]
    confidence: Any = None
    token_usage: dict[str, int] = None  # type: ignore[assignment]
    tool_calls: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.structured is None:
            self.structured = {}
        if self.token_usage is None:
            self.token_usage = {"total_tokens": 500}
        if self.tool_calls is None:
            self.tool_calls = []


@dataclass
class _FakeTaskCompleted:
    event_type: str = "task.completed"
    run_id: str = "run-001"
    task: Any = None
    result: Any = None


@dataclass
class _FakeTaskFailed:
    event_type: str = "task.failed"
    run_id: str = "run-001"
    task: Any = None
    error: str = ""


@dataclass
class _FakeRunCompleted:
    event_type: str = "run.completed"
    run_id: str = "run-001"
    summary: Any = None


@dataclass
class _FakeSummary:
    run_id: str = "run-001"
    done_count: int = 8
    failed_count: int = 2
    abandoned_count: int = 0
    total_tasks: int = 10


# ── Construction ─────────────────────────────────────────────────────────────


class TestBehavioralFingerprintConstruction:
    def test_creates_with_defaults(self) -> None:
        hook = BehavioralFingerprintHook()
        assert hook.id == "behavioral_fingerprint"
        assert hook.priority == 88

    def test_creates_with_custom_threshold(self) -> None:
        hook = BehavioralFingerprintHook(similarity_threshold=0.90)
        assert hook._similarity_threshold == 0.90

    def test_rejects_threshold_below_zero(self) -> None:
        with pytest.raises(VeridianConfigError, match="threshold"):
            BehavioralFingerprintHook(similarity_threshold=-0.1)

    def test_rejects_threshold_above_one(self) -> None:
        with pytest.raises(VeridianConfigError, match="threshold"):
            BehavioralFingerprintHook(similarity_threshold=1.5)


# ── Fingerprint Computation ─────────────────────────────────────────────────


class TestFingerprintComputation:
    def test_computes_7_dimensional_fingerprint(self, tmp_path: Path) -> None:
        hook = BehavioralFingerprintHook(history_file=tmp_path / "fp.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}", verifier_id="schema")
            result = _FakeResult(
                confidence=_FakeConfidence(composite=0.85),
                token_usage={"total_tokens": 500},
                tool_calls=["bash", "read"],
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        fp = hook.last_fingerprint
        assert fp is not None
        assert len(fp.dimensions) == 7

    def test_fingerprint_values_between_zero_and_one(self, tmp_path: Path) -> None:
        hook = BehavioralFingerprintHook(history_file=tmp_path / "fp.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(5):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(confidence=_FakeConfidence())
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        fp = hook.last_fingerprint
        assert fp is not None
        for val in fp.dimensions.values():
            assert 0.0 <= val <= 1.0

    def test_empty_run_produces_zero_fingerprint(self, tmp_path: Path) -> None:
        hook = BehavioralFingerprintHook(history_file=tmp_path / "fp.jsonl")
        hook.before_run(_FakeRunStarted(total_tasks=0))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary(total_tasks=0, done_count=0)))
        fp = hook.last_fingerprint
        assert fp is not None
        assert all(v == 0.0 for v in fp.dimensions.values())


# ── Cosine Similarity ───────────────────────────────────────────────────────


class TestCosineSimilarity:
    def test_identical_fingerprints_have_similarity_one(self) -> None:
        fp = BehavioralFingerprint(
            run_id="r1",
            dimensions={"a": 0.5, "b": 0.8, "c": 0.3},
        )
        assert fp.cosine_similarity(fp) == pytest.approx(1.0, abs=0.001)

    def test_orthogonal_fingerprints_have_similarity_zero(self) -> None:
        fp1 = BehavioralFingerprint(run_id="r1", dimensions={"a": 1.0, "b": 0.0})
        fp2 = BehavioralFingerprint(run_id="r2", dimensions={"a": 0.0, "b": 1.0})
        assert fp1.cosine_similarity(fp2) == pytest.approx(0.0, abs=0.001)

    def test_similar_fingerprints_have_high_similarity(self) -> None:
        fp1 = BehavioralFingerprint(run_id="r1", dimensions={"a": 0.8, "b": 0.7, "c": 0.6})
        fp2 = BehavioralFingerprint(run_id="r2", dimensions={"a": 0.85, "b": 0.72, "c": 0.58})
        assert fp1.cosine_similarity(fp2) > 0.95

    def test_zero_vector_returns_zero_similarity(self) -> None:
        fp1 = BehavioralFingerprint(run_id="r1", dimensions={"a": 0.0, "b": 0.0})
        fp2 = BehavioralFingerprint(run_id="r2", dimensions={"a": 0.5, "b": 0.5})
        assert fp1.cosine_similarity(fp2) == 0.0


# ── Divergence Detection ────────────────────────────────────────────────────


class TestDivergenceDetection:
    def test_detects_significant_divergence(self, tmp_path: Path) -> None:
        """When fingerprint shifts drastically, report should flag it."""
        history_file = tmp_path / "fp.jsonl"
        # Write a historical fingerprint with very different values
        old_fp = {
            "run_id": "old-run",
            "timestamp": "2026-01-01T00:00:00",
            "dimensions": {
                "action_distribution": 0.9,
                "output_structure": 0.85,
                "token_profile": 0.8,
                "verification_pattern": 0.75,
                "tool_selection": 0.95,
                "latency_profile": 0.7,
                "confidence_distribution": 0.88,
            },
        }
        history_file.write_text(json.dumps(old_fp) + "\n")

        hook = BehavioralFingerprintHook(
            history_file=history_file,
            similarity_threshold=0.85,
        )
        hook.before_run(_FakeRunStarted())

        # All tasks fail -> very different fingerprint
        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            hook.on_failure(_FakeTaskFailed(task=task, error="crash"))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary(done_count=0, failed_count=10)))
        report = hook.last_report
        assert report is not None
        assert report.divergence_detected is True

    def test_no_divergence_on_first_run(self, tmp_path: Path) -> None:
        hook = BehavioralFingerprintHook(history_file=tmp_path / "fp.jsonl")
        hook.before_run(_FakeRunStarted())
        for i in range(5):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(confidence=_FakeConfidence())
            hook.after_task(_FakeTaskCompleted(task=task, result=result))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        assert report.divergence_detected is False


# ── Persistence ─────────────────────────────────────────────────────────────


class TestFingerprintPersistence:
    def test_persists_fingerprint_to_jsonl(self, tmp_path: Path) -> None:
        fp_file = tmp_path / "fp.jsonl"
        hook = BehavioralFingerprintHook(history_file=fp_file)
        hook.before_run(_FakeRunStarted())
        task = _FakeTask(id="t1")
        result = _FakeResult(confidence=_FakeConfidence())
        hook.after_task(_FakeTaskCompleted(task=task, result=result))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))

        assert fp_file.exists()
        data = json.loads(fp_file.read_text().strip().split("\n")[-1])
        assert "dimensions" in data

    def test_no_temp_files_left(self, tmp_path: Path) -> None:
        fp_file = tmp_path / "fp.jsonl"
        hook = BehavioralFingerprintHook(history_file=fp_file)
        hook.before_run(_FakeRunStarted())
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        assert not list(tmp_path.glob("*.tmp"))

    def test_loads_history(self, tmp_path: Path) -> None:
        fp_file = tmp_path / "fp.jsonl"
        entry = {
            "run_id": "old",
            "timestamp": "2026-01-01",
            "dimensions": {"a": 0.5, "b": 0.6},
        }
        fp_file.write_text(json.dumps(entry) + "\n")

        hook = BehavioralFingerprintHook(history_file=fp_file)
        hook.before_run(_FakeRunStarted())
        assert len(hook._history) == 1


# ── Report ──────────────────────────────────────────────────────────────────


class TestFingerprintReport:
    def test_report_to_dict(self) -> None:
        report = FingerprintReport(
            run_id="r1",
            cosine_similarity=0.72,
            threshold=0.85,
            divergence_detected=True,
            dimensions_changed=["action_distribution", "token_profile"],
        )
        d = report.to_dict()
        assert d["divergence_detected"] is True
        assert d["cosine_similarity"] == 0.72

    def test_report_to_markdown(self) -> None:
        report = FingerprintReport(
            run_id="r1",
            cosine_similarity=0.72,
            threshold=0.85,
            divergence_detected=True,
            dimensions_changed=["action_distribution"],
        )
        md = report.to_markdown()
        assert "divergence" in md.lower() or "0.72" in md
