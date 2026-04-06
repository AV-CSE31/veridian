"""
Tests for veridian.eval.sandbox and veridian.eval.comparator
────────────────────────────────────────────────────────────
Evolution Sandbox — Bayesian A/B comparison of agent versions.
TDD: RED phase.
"""

from __future__ import annotations

from veridian.eval.comparator import EvolutionComparator
from veridian.eval.sandbox import EvolutionSandbox, SandboxResult
from veridian.hooks.builtin.drift_detector import RunSnapshot

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_snapshot(
    run_id: str = "r1",
    done: int = 8,
    failed: int = 2,
    total: int = 10,
    confidence_mean: float = 0.85,
    retry_rate: float = 0.1,
    mean_tokens: float = 500.0,
) -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        timestamp="2026-03-30T00:00:00",
        total_tasks=total,
        done_count=done,
        failed_count=failed,
        abandoned_count=0,
        confidence_mean=confidence_mean,
        confidence_std=0.05,
        retry_rate=retry_rate,
        mean_tokens_per_task=mean_tokens,
        completion_rate=done / max(total, 1),
    )


# ── EvolutionComparator ─────────────────────────────────────────────────────


class TestEvolutionComparator:
    def test_upgrade_when_version_b_better(self) -> None:
        """Version B clearly better -> UPGRADE recommendation."""
        snap_a = _make_snapshot(run_id="a", done=7, failed=3, confidence_mean=0.75)
        snap_b = _make_snapshot(run_id="b", done=9, failed=1, confidence_mean=0.92)

        comp = EvolutionComparator()
        result = comp.compare(snap_a, snap_b)
        assert result.recommendation == "upgrade"
        assert result.confidence > 0.5

    def test_hold_when_similar_performance(self) -> None:
        """Similar stats -> HOLD recommendation."""
        snap_a = _make_snapshot(run_id="a", done=8, failed=2, confidence_mean=0.85)
        snap_b = _make_snapshot(run_id="b", done=8, failed=2, confidence_mean=0.84)

        comp = EvolutionComparator()
        result = comp.compare(snap_a, snap_b)
        assert result.recommendation == "hold"

    def test_rollback_when_version_b_worse(self) -> None:
        """Version B clearly worse -> ROLLBACK recommendation."""
        snap_a = _make_snapshot(run_id="a", done=9, failed=1, confidence_mean=0.92)
        snap_b = _make_snapshot(run_id="b", done=5, failed=5, confidence_mean=0.55)

        comp = EvolutionComparator()
        result = comp.compare(snap_a, snap_b)
        assert result.recommendation == "rollback"

    def test_comparison_result_has_per_metric_breakdown(self) -> None:
        snap_a = _make_snapshot(run_id="a")
        snap_b = _make_snapshot(run_id="b")

        comp = EvolutionComparator()
        result = comp.compare(snap_a, snap_b)
        assert isinstance(result.metric_comparisons, dict)
        assert "completion_rate" in result.metric_comparisons

    def test_comparison_result_to_dict(self) -> None:
        snap_a = _make_snapshot(run_id="a")
        snap_b = _make_snapshot(run_id="b")

        comp = EvolutionComparator()
        result = comp.compare(snap_a, snap_b)
        d = result.to_dict()
        assert "recommendation" in d
        assert "confidence" in d
        assert "metric_comparisons" in d

    def test_custom_thresholds(self) -> None:
        """Custom upgrade/rollback thresholds work."""
        snap_a = _make_snapshot(run_id="a", done=8, confidence_mean=0.85)
        snap_b = _make_snapshot(run_id="b", done=9, confidence_mean=0.88)

        # Very strict threshold — minor improvement doesn't trigger upgrade
        comp = EvolutionComparator(upgrade_threshold=0.20, rollback_threshold=0.20)
        result = comp.compare(snap_a, snap_b)
        assert result.recommendation == "hold"


# ── EvolutionSandbox ────────────────────────────────────────────────────────


class TestEvolutionSandbox:
    def test_creates_sandbox_with_task_suite(self) -> None:
        tasks = [
            {"id": "t1", "title": "Test task 1"},
            {"id": "t2", "title": "Test task 2"},
        ]
        sandbox = EvolutionSandbox(task_suite=tasks)
        assert len(sandbox.task_suite) == 2

    def test_evaluate_returns_sandbox_result(self) -> None:
        """Evaluate with two snapshots returns a SandboxResult."""
        snap_a = _make_snapshot(run_id="a")
        snap_b = _make_snapshot(run_id="b")

        sandbox = EvolutionSandbox(task_suite=[])
        result = sandbox.evaluate(snapshot_a=snap_a, snapshot_b=snap_b)
        assert isinstance(result, SandboxResult)
        assert result.comparison is not None
        assert result.recommendation in ("upgrade", "hold", "rollback")

    def test_evaluate_with_canary_results(self) -> None:
        """Canary failures override comparison result."""
        snap_a = _make_snapshot(run_id="a", done=7, confidence_mean=0.75)
        snap_b = _make_snapshot(run_id="b", done=9, confidence_mean=0.92)

        sandbox = EvolutionSandbox(task_suite=[])
        result = sandbox.evaluate(
            snapshot_a=snap_a,
            snapshot_b=snap_b,
            canary_failures=["c1", "c2"],  # canary regression
        )
        # Even though B is better, canary failures force rollback
        assert result.recommendation == "rollback"

    def test_sandbox_result_to_dict(self) -> None:
        snap_a = _make_snapshot(run_id="a")
        snap_b = _make_snapshot(run_id="b")

        sandbox = EvolutionSandbox(task_suite=[])
        result = sandbox.evaluate(snapshot_a=snap_a, snapshot_b=snap_b)
        d = result.to_dict()
        assert "recommendation" in d
        assert "comparison" in d

    def test_sandbox_result_to_markdown(self) -> None:
        snap_a = _make_snapshot(run_id="a")
        snap_b = _make_snapshot(run_id="b")

        sandbox = EvolutionSandbox(task_suite=[])
        result = sandbox.evaluate(snapshot_a=snap_a, snapshot_b=snap_b)
        md = result.to_markdown()
        assert "version" in md.lower() or "recommendation" in md.lower()
