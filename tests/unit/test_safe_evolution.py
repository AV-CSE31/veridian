"""
Tests for veridian.protocols.safe_evolution — Safety-Aware Evolution Protocol.
TDD: RED phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from veridian.core.exceptions import EvolutionBlockedError
from veridian.eval.canary import CanaryReport, CanaryResult
from veridian.hooks.builtin.drift_detector import RunSnapshot
from veridian.protocols.safe_evolution import (
    EvolutionGate,
    EvolutionProposal,
    EvolutionOutcome,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_snapshot(
    run_id: str = "r1",
    done: int = 9,
    failed: int = 1,
    confidence_mean: float = 0.88,
) -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        timestamp="2026-03-31T00:00:00",
        total_tasks=10,
        done_count=done,
        failed_count=failed,
        confidence_mean=confidence_mean,
        completion_rate=done / 10,
        retry_rate=0.1,
        mean_tokens_per_task=500.0,
    )


def _make_canary_report(regressions: int = 0) -> CanaryReport:
    results = []
    for i in range(5):
        results.append(CanaryResult(
            task_id=f"canary-{i}",
            expected_pass=True,
            actual_pass=i >= regressions,
        ))
    return CanaryReport(results=results, run_id="canary-run")


# ── EvolutionGate ───────────────────────────────────────────────────────────


class TestEvolutionGate:
    def test_creates_gate(self) -> None:
        gate = EvolutionGate()
        assert gate is not None

    def test_approves_when_safer_and_capable(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(run_id="v1", done=8, confidence_mean=0.82),
            candidate_snapshot=_make_snapshot(run_id="v2", done=9, confidence_mean=0.90),
            canary_report=_make_canary_report(regressions=0),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        assert outcome.approved is True
        assert outcome.recommendation == "upgrade"

    def test_rejects_on_canary_regression(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(run_id="v1", done=7),
            candidate_snapshot=_make_snapshot(run_id="v2", done=9),
            canary_report=_make_canary_report(regressions=2),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        assert outcome.approved is False
        assert "canary" in outcome.reason.lower()

    def test_rejects_capability_up_safety_down(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(run_id="v1", done=7, confidence_mean=0.90),
            candidate_snapshot=_make_snapshot(run_id="v2", done=9, confidence_mean=0.50),
            canary_report=_make_canary_report(regressions=0),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        assert outcome.approved is False

    def test_holds_when_similar_performance(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(run_id="v1", done=8, confidence_mean=0.85),
            candidate_snapshot=_make_snapshot(run_id="v2", done=8, confidence_mean=0.84),
            canary_report=_make_canary_report(regressions=0),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        assert outcome.recommendation in ("hold", "upgrade")

    def test_outcome_to_dict(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(),
            candidate_snapshot=_make_snapshot(),
            canary_report=_make_canary_report(),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        d = outcome.to_dict()
        assert "approved" in d
        assert "recommendation" in d

    def test_outcome_to_markdown(self) -> None:
        proposal = EvolutionProposal(
            baseline_snapshot=_make_snapshot(),
            candidate_snapshot=_make_snapshot(),
            canary_report=_make_canary_report(),
        )
        gate = EvolutionGate()
        outcome = gate.evaluate(proposal)
        md = outcome.to_markdown()
        assert "evolution" in md.lower() or "recommendation" in md.lower()
