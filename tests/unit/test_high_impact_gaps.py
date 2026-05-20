"""Focused tests for the slim high-impact verifier set."""

from __future__ import annotations

import importlib.util


def test_confidence_score_degrades_across_retries() -> None:
    from veridian.verify.builtin.confidence import ConfidenceScore

    first = ConfidenceScore.compute(retry_count=0, max_retries=3)
    retry = ConfidenceScore.compute(retry_count=1, max_retries=3)

    assert first.composite > retry.composite


def test_cross_run_consistency_hook_stays_removed() -> None:
    assert importlib.util.find_spec("veridian.hooks.builtin.cross_run_consistency") is None

