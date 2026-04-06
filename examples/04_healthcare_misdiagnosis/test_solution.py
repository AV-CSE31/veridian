"""
Tests for Problem 4: Healthcare Misdiagnosis — Diagnostic Consensus.
Failure-first: prove single-sample misdiagnosis is blocked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


_solution = _load_local_module("solution.py", f"{Path(__file__).parent.name}_solution")
DiagnosticConsensusVerifier = _solution.DiagnosticConsensusVerifier

from veridian.core.task import Task, TaskResult


@pytest.fixture
def verifier() -> DiagnosticConsensusVerifier:
    return DiagnosticConsensusVerifier(min_agreement=0.80, min_samples=3)


def _task(tid: str = "t1") -> Task:
    return Task(id=tid, title="diagnose", verifier_id="diagnostic_consensus")


def _result(diagnoses: list[str]) -> TaskResult:
    return TaskResult(raw_output="", structured={"diagnoses": diagnoses})


class TestBlocksMisdiagnosis:
    """Prove the 66% misdiagnosis incident pattern is blocked."""

    def test_blocks_all_different_diagnoses(self, verifier: DiagnosticConsensusVerifier) -> None:
        """66% failure pattern: every sample gives a different answer."""
        r = verifier.verify(_task(), _result(["pneumonia", "flu", "bronchitis", "cold", "asthma"]))
        assert r.passed is False
        assert "agreement" in (r.error or "").lower()

    def test_blocks_split_decision(self, verifier: DiagnosticConsensusVerifier) -> None:
        """50/50 split — dangerous for clinical decisions."""
        r = verifier.verify(_task(), _result(["pneumonia", "pneumonia", "flu", "flu", "cold"]))
        assert r.passed is False

    def test_blocks_insufficient_samples(self, verifier: DiagnosticConsensusVerifier) -> None:
        """Single-sample diagnosis — the exact root cause of misdiagnosis."""
        r = verifier.verify(_task(), _result(["pneumonia"]))
        assert r.passed is False
        assert "insufficient" in (r.error or "").lower() or "need" in (r.error or "").lower()

    def test_blocks_empty_diagnoses(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result([]))
        assert r.passed is False

    def test_error_includes_distribution(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result(["a", "b", "c"]))
        assert r.passed is False
        assert "escalate" in (r.error or "").lower() or "distribution" in (r.error or "").lower()


class TestPassesValidDiagnosis:
    """Prove legitimate diagnoses with strong consensus pass."""

    def test_passes_unanimous_5_of_5(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result(["pneumonia"] * 5))
        assert r.passed is True
        assert r.evidence.get("consensus") == "pneumonia"
        assert r.evidence.get("agreement") == 1.0

    def test_passes_4_of_5_agreement(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result(["pneumonia"] * 4 + ["flu"]))
        assert r.passed is True

    def test_passes_3_of_3_unanimous(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result(["myocardial infarction"] * 3))
        assert r.passed is True

    def test_case_insensitive_matching(self, verifier: DiagnosticConsensusVerifier) -> None:
        r = verifier.verify(_task(), _result(["Pneumonia", "pneumonia", "PNEUMONIA"]))
        assert r.passed is True
