"""
Tests for Problem 5: Financial Cascade — AML Classification Verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from solution import AMLClassificationVerifier

from veridian.core.task import Task, TaskResult


@pytest.fixture
def verifier() -> AMLClassificationVerifier:
    return AMLClassificationVerifier()


def _task(tid: str = "t1") -> Task:
    return Task(id=tid, title="classify", verifier_id="aml_classification")


def _result(fields: dict[str, str]) -> TaskResult:
    return TaskResult(raw_output="", structured=fields)


def _valid(risk: str = "LOW", action: str = "CLEAR") -> dict[str, str]:
    return {"risk_level": risk, "action": action, "justification": "reason", "regulation_cited": "AML-2024"}


class TestBlocksCascadePatterns:
    """Prove financial misclassification cascade is blocked."""

    def test_blocks_low_risk_with_block_action(self, verifier: AMLClassificationVerifier) -> None:
        """The exact cascade pattern: LOW risk incorrectly paired with BLOCK."""
        r = verifier.verify(_task(), _result(_valid("LOW", "BLOCK")))
        assert r.passed is False
        assert "inconsistent" in (r.error or "").lower()

    def test_blocks_critical_risk_with_clear_action(self, verifier: AMLClassificationVerifier) -> None:
        """Catastrophic: CRITICAL risk cleared — sanctions violation."""
        r = verifier.verify(_task(), _result(_valid("CRITICAL", "CLEAR")))
        assert r.passed is False

    def test_blocks_high_risk_with_clear(self, verifier: AMLClassificationVerifier) -> None:
        r = verifier.verify(_task(), _result(_valid("HIGH", "CLEAR")))
        assert r.passed is False

    def test_blocks_medium_risk_with_block(self, verifier: AMLClassificationVerifier) -> None:
        r = verifier.verify(_task(), _result(_valid("MEDIUM", "BLOCK")))
        assert r.passed is False

    def test_blocks_missing_justification(self, verifier: AMLClassificationVerifier) -> None:
        r = verifier.verify(_task(), _result({"risk_level": "HIGH", "action": "ESCALATE"}))
        assert r.passed is False
        assert "missing" in (r.error or "").lower()

    def test_blocks_invalid_risk_level(self, verifier: AMLClassificationVerifier) -> None:
        r = verifier.verify(_task(), _result(_valid("UNKNOWN", "CLEAR")))
        assert r.passed is False
        assert "invalid" in (r.error or "").lower()

    def test_error_shows_allowed_actions(self, verifier: AMLClassificationVerifier) -> None:
        r = verifier.verify(_task(), _result(_valid("LOW", "BLOCK")))
        assert "CLEAR" in (r.error or "") or "FLAG" in (r.error or "")


class TestPassesValidClassifications:
    """Prove legitimate AML classifications pass."""

    def test_low_clear(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("LOW", "CLEAR"))).passed is True

    def test_low_flag(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("LOW", "FLAG"))).passed is True

    def test_medium_flag(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("MEDIUM", "FLAG"))).passed is True

    def test_medium_escalate(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("MEDIUM", "ESCALATE"))).passed is True

    def test_high_escalate(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("HIGH", "ESCALATE"))).passed is True

    def test_high_block(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("HIGH", "BLOCK"))).passed is True

    def test_critical_block(self, verifier: AMLClassificationVerifier) -> None:
        assert verifier.verify(_task(), _result(_valid("CRITICAL", "BLOCK"))).passed is True
