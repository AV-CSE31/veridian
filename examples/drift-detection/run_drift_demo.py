#!/usr/bin/env python3
"""
Drift Detection Demo
────────────────────
Simulates stable → degraded agent runs and shows DriftDetectorHook
catching the behavioral regression in real time.

Usage:
    python examples/drift-detection/run_drift_demo.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from veridian.core.events import RunCompleted, RunStarted, TaskCompleted, TaskFailed
from veridian.hooks.builtin.drift_detector import DriftDetectorHook

# ── Fake objects (same pattern as test suite) ────────────────────────────────


class _FakeTask:
    def __init__(self, id: str, verifier_id: str = "schema", retry_count: int = 0):
        self.id = id
        self.verifier_id = verifier_id
        self.retry_count = retry_count


class _FakeConfidence:
    def __init__(self, composite: float):
        self.composite = composite


class _FakeResult:
    def __init__(self, tokens: int = 1000, confidence: float = 0.88):
        self.token_usage = {"total_tokens": tokens}
        self.confidence = _FakeConfidence(confidence)


class _FakeSummary:
    def __init__(self, run_id: str, done: int, failed: int, total: int):
        self.run_id = run_id
        self.done_count = done
        self.failed_count = failed
        self.abandoned_count = 0
        self.total_tasks = total


# ── Pre-populate stable history ──────────────────────────────────────────────


def _stable_snapshot(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "timestamp": "2026-03-20T10:00:00",
        "total_tasks": 10,
        "done_count": 9,
        "failed_count": 1,
        "abandoned_count": 0,
        "verifier_stats": {"schema": {"pass": 9, "fail": 1}},
        "confidence_mean": 0.88,
        "confidence_std": 0.04,
        "confidence_tier_counts": {"HIGH": 9, "MEDIUM": 1, "LOW": 0, "UNCERTAIN": 0},
        "retry_rate": 0.10,
        "mean_tokens_per_task": 1000.0,
        "completion_rate": 0.90,
        "failure_modes": {},
    }


def _simulate_run(
    hook: DriftDetectorHook,
    run_id: str,
    pass_count: int,
    fail_count: int,
    confidence: float = 0.88,
    tokens: int = 1000,
    retry_count: int = 0,
) -> None:
    """Simulate a full run lifecycle through the hook."""
    total = pass_count + fail_count
    hook.before_run(RunStarted(run_id=run_id, total_tasks=total))

    for i in range(pass_count):
        event = TaskCompleted(
            run_id=run_id,
            task=_FakeTask(f"t_pass_{i}", retry_count=retry_count),
            result=_FakeResult(tokens=tokens, confidence=confidence),
        )
        hook.after_task(event)

    for i in range(fail_count):
        event = TaskFailed(
            run_id=run_id,
            task=_FakeTask(f"t_fail_{i}"),
            error="field 'risk_level' missing",
            attempt=1,
        )
        hook.on_failure(event)

    summary = _FakeSummary(run_id, pass_count, fail_count, total)
    hook.after_run(RunCompleted(run_id=run_id, summary=summary))


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="veridian_drift_"))
    history_file = tmp / "drift_history.jsonl"
    report_path = tmp / "drift_report.md"

    print("=" * 60)
    print("  Veridian Drift Detection Demo")
    print("=" * 60)
    print()

    # Step 1: Write stable baseline history
    print("[1/3] Pre-populating 7 stable runs (90% pass rate)...")
    with open(history_file, "w") as f:
        for i in range(7):
            f.write(json.dumps(_stable_snapshot(f"stable_{i}")) + "\n")
    print(f"      History: {history_file}")
    print()

    # Step 2: Simulate degraded runs
    print("[2/3] Simulating 3 degraded runs (70% pass, low confidence)...")
    print()

    for i in range(3):
        run_id = f"degraded_{i}"
        hook = DriftDetectorHook(
            history_file=history_file,
            window=5,
            threshold=0.15,
            report_path=report_path,
        )

        _simulate_run(
            hook,
            run_id=run_id,
            pass_count=7,
            fail_count=3,
            confidence=0.55,
            tokens=2500,
            retry_count=2,
        )

        report = hook.last_report
        if report:
            status = report.overall_status.upper()
            n_signals = len(report.signals)
            print(f"  Run '{run_id}': {status} ({n_signals} signals)")
            for s in report.signals:
                print(
                    f"    - {s.metric}: {s.baseline_mean:.2f} -> "
                    f"{s.current_value:.2f} (z={s.z_score:.1f}, {s.direction})"
                )
            if report.recommended_actions:
                print("    Actions:")
                for action in report.recommended_actions:
                    print(f"      * {action}")
            print()

    # Step 3: Show report
    print("[3/3] Drift report written to:")
    print(f"      {report_path}")
    print()
    print("-" * 60)
    print(report_path.read_text())


if __name__ == "__main__":
    main()
