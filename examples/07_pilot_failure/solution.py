"""
Problem 7: 95% AI Pilot Failure — Silent Behavioral Drift
==========================================================
How Veridian detects degradation before production breaks.

INCIDENT:
  MIT study: 95% of enterprise AI pilots deliver zero measurable return.
  73% of supply chain AI failures stem from incomplete data visibility.

  arXiv 2601.04170 ("Agent Drift"): quantifies three types of drift —
  semantic drift, data drift, concept drift. Finds that reliability
  doesn't improve uniformly with capability.

  Chanl AI: "Drift is a month-three-plus problem. Launch testing misses it."

  The pattern: agent works perfectly for weeks, then pass rates silently
  erode from 95% to 72%. Token consumption doubles. Confidence drops.
  Nobody notices until production breaks or a quarterly review surfaces it.

VERIDIAN'S FIX:
  DriftDetectorHook — Bayesian regression comparing current run metrics
  against a historical baseline window. Detects statistically significant
  shifts in completion rate, confidence, retry rate, and token usage.

  BehavioralFingerprint — 7-dimensional per-run signature. Catches subtle
  behavioral shifts (different tools used, different output structure) that
  aggregate metrics miss. Cosine similarity below threshold = alert.

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import time

from veridian.hooks.builtin.drift_detector import (
    DriftDetectorHook,
    DriftReport,
    RunSnapshot,
)
from veridian.hooks.builtin.behavioral_fingerprint import (
    BehavioralFingerprint,
)


def simulate_drift() -> DriftReport:
    """Simulate drift detection using Veridian's real analysis engine.

    Builds 5 healthy baseline runs, then feeds a degraded run through
    the same Bayesian analysis that runs in production.
    """
    history = [
        RunSnapshot(
            run_id=f"healthy-{i}", timestamp=f"2026-03-{20+i}T00:00:00",
            total_tasks=100, done_count=95, failed_count=5,
            confidence_mean=0.88, confidence_std=0.05,
            retry_rate=0.08, mean_tokens_per_task=450.0, completion_rate=0.95,
        )
        for i in range(5)
    ]

    degraded = RunSnapshot(
        run_id="degraded", timestamp="2026-03-28T00:00:00",
        total_tasks=100, done_count=72, failed_count=28,
        confidence_mean=0.61, confidence_std=0.15,
        retry_rate=0.35, mean_tokens_per_task=890.0, completion_rate=0.72,
    )

    hook = DriftDetectorHook(window=5, threshold=0.15, z_threshold=2.0)
    return hook._analyze_drift(degraded, history)


def simulate_fingerprint_divergence() -> float:
    """Compute cosine similarity between healthy and drifted fingerprints."""
    baseline = BehavioralFingerprint(
        run_id="baseline",
        dimensions={
            "action_distribution": 0.85, "output_structure": 0.90,
            "token_profile": 0.45, "verification_pattern": 0.12,
            "tool_selection": 0.78, "latency_profile": 0.92,
            "confidence_distribution": 0.88,
        },
    )

    shifted = BehavioralFingerprint(
        run_id="shifted",
        dimensions={
            "action_distribution": 0.20, "output_structure": 0.15,
            "token_profile": 0.90, "verification_pattern": 0.75,
            "tool_selection": 0.10, "latency_profile": 0.40,
            "confidence_distribution": 0.25,
        },
    )

    return baseline.cosine_similarity(shifted)


def run_demo() -> None:
    start = time.monotonic()

    print(f"\n{'=' * 65}")
    print("  VERIDIAN -- Behavioral Drift and Fingerprint Detection")
    print("  Real DriftDetectorHook + BehavioralFingerprint")
    print(f"{'=' * 65}")

    # Drift detection
    report = simulate_drift()
    print(f"\n  Drift Analysis (Veridian DriftDetectorHook)")
    print(f"  {'-' * 55}")
    print(f"  Overall status: {report.overall_status.upper()}")
    print(f"  Signals found:  {len(report.signals)}")

    if report.signals:
        print(f"\n  {'Metric':<30s} {'Baseline':>10s} {'Current':>10s} {'Direction':<12s}")
        print(f"  {'-' * 62}")
        for s in report.signals:
            print(f"  {s.metric:<30s} {s.baseline_mean:>10.4f} {s.current_value:>10.4f} {s.direction:<12s}")

    if report.recommended_actions:
        print(f"\n  Recommended Actions:")
        for a in report.recommended_actions:
            print(f"    -> {a[:70]}")

    # Fingerprint divergence
    similarity = simulate_fingerprint_divergence()
    print(f"\n  Fingerprint Analysis (Veridian BehavioralFingerprint)")
    print(f"  {'-' * 55}")
    print(f"  Cosine similarity: {similarity:.4f}")
    print(f"  Threshold:         0.85")
    print(f"  Divergence:        {'DETECTED' if similarity < 0.85 else 'none'}")

    elapsed = int((time.monotonic() - start) * 1000)
    print(f"\n  Elapsed: {elapsed}ms")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    run_demo()
