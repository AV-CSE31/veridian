"""
tests.unit.test_drift_detector
──────────────────────────────
DriftDetectorHook: behavioral regression detection across runs.
Written FIRST per TDD mandate (CLAUDE.md §1.1).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from veridian.core.events import (
    DriftWarning,
    RunCompleted,
    RunStarted,
    TaskCompleted,
    TaskFailed,
)
from veridian.core.exceptions import VeridianConfigError
from veridian.hooks.builtin.drift_detector import (
    DriftDetectorHook,
    DriftReport,
    DriftSignal,
    RunSnapshot,
)
from veridian.hooks.registry import HookRegistry


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_task_completed(
    run_id: str = "r1",
    task_id: str = "t1",
    verifier_id: str = "schema",
    confidence_composite: float = 0.90,
    token_usage: dict[str, int] | None = None,
    retry_count: int = 0,
) -> TaskCompleted:
    """Build a TaskCompleted event with mock task and result."""
    task = _FakeTask(
        id=task_id,
        verifier_id=verifier_id,
        retry_count=retry_count,
    )
    result = _FakeResult(
        token_usage=token_usage or {"total_tokens": 1000},
        confidence_composite=confidence_composite,
    )
    return TaskCompleted(run_id=run_id, task=task, result=result)


def _make_task_failed(
    run_id: str = "r1",
    task_id: str = "t1",
    verifier_id: str = "schema",
    error: str = "field 'risk_level' missing",
    attempt: int = 1,
) -> TaskFailed:
    """Build a TaskFailed event with mock task."""
    task = _FakeTask(id=task_id, verifier_id=verifier_id)
    return TaskFailed(run_id=run_id, task=task, error=error, attempt=attempt)


class _FakeTask:
    """Minimal task mock for hook tests."""

    def __init__(
        self,
        id: str = "t1",
        verifier_id: str = "schema",
        retry_count: int = 0,
    ) -> None:
        self.id = id
        self.verifier_id = verifier_id
        self.retry_count = retry_count


class _FakeResult:
    """Minimal result mock for hook tests."""

    def __init__(
        self,
        token_usage: dict[str, int] | None = None,
        confidence_composite: float = 0.90,
    ) -> None:
        self.token_usage = token_usage or {"total_tokens": 1000}
        self.confidence = _FakeConfidence(confidence_composite)


class _FakeConfidence:
    """Minimal confidence mock."""

    def __init__(self, composite: float = 0.90) -> None:
        self.composite = composite


class _FakeSummary:
    """Minimal RunSummary mock."""

    def __init__(
        self,
        run_id: str = "r1",
        done_count: int = 10,
        failed_count: int = 0,
        abandoned_count: int = 0,
        total_tasks: int = 10,
    ) -> None:
        self.run_id = run_id
        self.done_count = done_count
        self.failed_count = failed_count
        self.abandoned_count = abandoned_count
        self.total_tasks = total_tasks


def _stable_snapshot(
    run_id: str = "r_stable",
    pass_rate: float = 0.90,
    total: int = 10,
    confidence_mean: float = 0.88,
) -> dict[str, Any]:
    """Generate a stable RunSnapshot dict for history pre-population."""
    passes = int(total * pass_rate)
    fails = total - passes
    return {
        "run_id": run_id,
        "timestamp": "2026-03-20T10:00:00",
        "total_tasks": total,
        "done_count": passes,
        "failed_count": fails,
        "abandoned_count": 0,
        "verifier_stats": {"schema": {"pass": passes, "fail": fails}},
        "confidence_mean": confidence_mean,
        "confidence_std": 0.05,
        "confidence_tier_counts": {"HIGH": passes, "MEDIUM": 0, "LOW": 0, "UNCERTAIN": 0},
        "retry_rate": 0.10,
        "mean_tokens_per_task": 1000.0,
        "completion_rate": pass_rate,
        "failure_modes": {},
    }


def _write_history(path: Path, snapshots: list[dict[str, Any]]) -> None:
    """Write pre-populated history JSONL."""
    with open(path, "w") as f:
        for snap in snapshots:
            f.write(json.dumps(snap) + "\n")


# ── RunSnapshot tests ────────────────────────────────────────────────────────


class TestRunSnapshot:
    """RunSnapshot serialization round-trip."""

    def test_to_dict_roundtrip(self) -> None:
        """Should serialize and deserialize without data loss."""
        snap = RunSnapshot(
            run_id="r1",
            timestamp="2026-03-20T10:00:00",
            total_tasks=10,
            done_count=9,
            failed_count=1,
            abandoned_count=0,
            verifier_stats={"schema": {"pass": 9, "fail": 1}},
            confidence_mean=0.88,
            confidence_std=0.05,
            confidence_tier_counts={"HIGH": 9, "MEDIUM": 1, "LOW": 0, "UNCERTAIN": 0},
            retry_rate=0.10,
            mean_tokens_per_task=1000.0,
            completion_rate=0.90,
            failure_modes={},
        )
        d = snap.to_dict()
        restored = RunSnapshot.from_dict(d)
        assert restored.run_id == "r1"
        assert restored.done_count == 9
        assert restored.verifier_stats == {"schema": {"pass": 9, "fail": 1}}
        assert restored.confidence_mean == pytest.approx(0.88)

    def test_from_dict_missing_fields_uses_defaults(self) -> None:
        """Should handle partial dicts gracefully with defaults."""
        d: dict[str, Any] = {"run_id": "r1", "timestamp": "2026-03-20T10:00:00"}
        snap = RunSnapshot.from_dict(d)
        assert snap.run_id == "r1"
        assert snap.total_tasks == 0
        assert snap.failure_modes == {}


# ── DriftSignal tests ────────────────────────────────────────────────────────


class TestDriftSignal:
    """DriftSignal dataclass."""

    def test_degraded_direction(self) -> None:
        """Should flag degradation when current is worse than baseline."""
        signal = DriftSignal(
            metric="verification_pass_rate.schema",
            baseline_mean=0.90,
            baseline_std=0.03,
            current_value=0.70,
            z_score=-6.67,
            magnitude=0.22,
            direction="degraded",
            significance="significant",
        )
        assert signal.direction == "degraded"
        assert signal.significance == "significant"

    def test_improved_direction(self) -> None:
        """Should flag improvement when current is better than baseline."""
        signal = DriftSignal(
            metric="confidence_mean",
            baseline_mean=0.80,
            baseline_std=0.05,
            current_value=0.95,
            z_score=3.0,
            magnitude=0.1875,
            direction="improved",
            significance="significant",
        )
        assert signal.direction == "improved"


# ── DriftDetectorHook tests ─────────────────────────────────────────────────


class TestDriftDetectorHook:
    """DriftDetectorHook: behavioral regression detection."""

    @pytest.fixture
    def hook(self, tmp_path: Path) -> DriftDetectorHook:
        """Standard hook with tmp_path history file."""
        return DriftDetectorHook(
            history_file=tmp_path / "drift_history.jsonl",
            window=5,
            threshold=0.15,
        )

    # ── Snapshot collection ──────────────────────────────────────────────

    def test_collects_pass_counts_from_task_completed(
        self, hook: DriftDetectorHook
    ) -> None:
        """Should accumulate pass count per verifier_id."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.after_task(_make_task_completed(verifier_id="schema"))
        hook.after_task(_make_task_completed(verifier_id="schema"))
        assert hook._verifier_pass_counts["schema"] == 2

    def test_collects_fail_counts_from_task_failed(
        self, hook: DriftDetectorHook
    ) -> None:
        """Should accumulate fail count per verifier_id."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.on_failure(_make_task_failed(verifier_id="schema"))
        assert hook._verifier_fail_counts["schema"] == 1

    def test_collects_token_usage(self, hook: DriftDetectorHook) -> None:
        """Should accumulate total tokens across tasks."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.after_task(_make_task_completed(token_usage={"total_tokens": 500}))
        hook.after_task(_make_task_completed(token_usage={"total_tokens": 1500}))
        assert hook._total_tokens == 2000

    def test_collects_confidence_scores(self, hook: DriftDetectorHook) -> None:
        """Should accumulate confidence composite scores."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.after_task(_make_task_completed(confidence_composite=0.85))
        hook.after_task(_make_task_completed(confidence_composite=0.95))
        assert len(hook._confidence_scores) == 2
        assert hook._confidence_scores[0] == pytest.approx(0.85)

    def test_builds_snapshot_on_after_run(
        self, hook: DriftDetectorHook, tmp_path: Path
    ) -> None:
        """Should build and persist a RunSnapshot on after_run."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.after_task(_make_task_completed(verifier_id="schema"))
        hook.on_failure(_make_task_failed(verifier_id="schema"))
        summary = _FakeSummary(done_count=1, failed_count=1, total_tasks=2)
        hook.after_run(RunCompleted(run_id="r1", summary=summary))

        history_file = tmp_path / "drift_history.jsonl"
        assert history_file.exists()
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 1
        snap = json.loads(lines[0])
        assert snap["run_id"] == "r1"
        assert snap["verifier_stats"]["schema"]["pass"] == 1
        assert snap["verifier_stats"]["schema"]["fail"] == 1

    # ── Drift detection ──────────────────────────────────────────────────

    def test_detects_pass_rate_drop(
        self, tmp_path: Path
    ) -> None:
        """Should detect significant pass rate degradation."""
        history_file = tmp_path / "drift_history.jsonl"
        # Pre-populate with 7 stable runs at 90% pass rate
        _write_history(
            history_file,
            [_stable_snapshot(f"r_stable_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate a degraded run: 70% pass rate
        hook.before_run(RunStarted(run_id="r_bad", total_tasks=10))
        for i in range(7):
            hook.after_task(_make_task_completed(
                task_id=f"t_pass_{i}", verifier_id="schema"
            ))
        for i in range(3):
            hook.on_failure(_make_task_failed(
                task_id=f"t_fail_{i}", verifier_id="schema"
            ))
        summary = _FakeSummary(done_count=7, failed_count=3, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_bad", summary=summary))

        report = hook.last_report
        assert report is not None
        assert report.overall_status in ("warning", "drifting")
        pass_rate_signals = [
            s for s in report.signals if "pass_rate" in s.metric
        ]
        assert len(pass_rate_signals) > 0
        assert pass_rate_signals[0].direction == "degraded"

    def test_no_false_positive_on_stable_runs(
        self, tmp_path: Path
    ) -> None:
        """Should report 'stable' when current run matches baseline."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_stable_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate a normal run: 90% pass rate (matches baseline)
        hook.before_run(RunStarted(run_id="r_ok", total_tasks=10))
        for i in range(9):
            hook.after_task(_make_task_completed(
                task_id=f"t_pass_{i}", verifier_id="schema"
            ))
        hook.on_failure(_make_task_failed(task_id="t_fail_0", verifier_id="schema"))
        summary = _FakeSummary(done_count=9, failed_count=1, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_ok", summary=summary))

        report = hook.last_report
        assert report is not None
        assert report.overall_status == "stable"

    def test_detects_confidence_degradation(
        self, tmp_path: Path
    ) -> None:
        """Should detect drop in confidence scores."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}", confidence_mean=0.90) for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate low-confidence run
        hook.before_run(RunStarted(run_id="r_low", total_tasks=10))
        for i in range(10):
            hook.after_task(_make_task_completed(
                task_id=f"t_{i}", confidence_composite=0.55
            ))
        summary = _FakeSummary(done_count=10, failed_count=0, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_low", summary=summary))

        report = hook.last_report
        assert report is not None
        confidence_signals = [
            s for s in report.signals if "confidence" in s.metric
        ]
        assert len(confidence_signals) > 0
        assert confidence_signals[0].direction == "degraded"

    def test_detects_retry_rate_increase(
        self, tmp_path: Path
    ) -> None:
        """Should detect increased retry rate."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate high-retry run
        hook.before_run(RunStarted(run_id="r_retry", total_tasks=10))
        for i in range(10):
            hook.after_task(_make_task_completed(
                task_id=f"t_{i}", retry_count=3
            ))
        summary = _FakeSummary(done_count=10, failed_count=0, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_retry", summary=summary))

        report = hook.last_report
        assert report is not None
        retry_signals = [s for s in report.signals if "retry" in s.metric]
        assert len(retry_signals) > 0

    def test_detects_token_consumption_increase(
        self, tmp_path: Path
    ) -> None:
        """Should detect token usage spike (context degradation signal)."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate high-token run
        hook.before_run(RunStarted(run_id="r_tokens", total_tasks=10))
        for i in range(10):
            hook.after_task(_make_task_completed(
                task_id=f"t_{i}", token_usage={"total_tokens": 3000}
            ))
        summary = _FakeSummary(done_count=10, failed_count=0, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_tokens", summary=summary))

        report = hook.last_report
        assert report is not None
        token_signals = [s for s in report.signals if "token" in s.metric]
        assert len(token_signals) > 0

    def test_detects_failure_mode_clustering(
        self, tmp_path: Path
    ) -> None:
        """Should flag when one error dominates failures."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Simulate run where same error keeps appearing
        hook.before_run(RunStarted(run_id="r_cluster", total_tasks=10))
        for i in range(5):
            hook.after_task(_make_task_completed(task_id=f"t_pass_{i}"))
        for i in range(5):
            hook.on_failure(_make_task_failed(
                task_id=f"t_fail_{i}",
                error="field 'risk_level' missing",
            ))
        summary = _FakeSummary(done_count=5, failed_count=5, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_cluster", summary=summary))

        report = hook.last_report
        assert report is not None
        cluster_signals = [
            s for s in report.signals if "failure_mode" in s.metric
        ]
        assert len(cluster_signals) > 0

    # ── Persistence ──────────────────────────────────────────────────────

    def test_persists_snapshot_atomically(
        self, hook: DriftDetectorHook, tmp_path: Path
    ) -> None:
        """Should persist snapshot with no temp files left behind."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=1))
        hook.after_task(_make_task_completed())
        summary = _FakeSummary(done_count=1, total_tasks=1)
        hook.after_run(RunCompleted(run_id="r1", summary=summary))

        history_file = tmp_path / "drift_history.jsonl"
        assert history_file.exists()
        assert not list(tmp_path.glob("*.tmp"))

    def test_loads_history_from_existing_file(
        self, tmp_path: Path
    ) -> None:
        """Should load and parse existing JSONL history."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(history_file, [_stable_snapshot("r1"), _stable_snapshot("r2")])

        hook = DriftDetectorHook(history_file=history_file, window=5)
        hook.before_run(RunStarted(run_id="r3", total_tasks=1))
        # History should be loaded
        assert len(hook._history) == 2

    def test_handles_corrupted_history_gracefully(
        self, tmp_path: Path
    ) -> None:
        """Should skip corrupted lines and continue."""
        history_file = tmp_path / "drift_history.jsonl"
        with open(history_file, "w") as f:
            f.write(json.dumps(_stable_snapshot("r1")) + "\n")
            f.write("NOT VALID JSON\n")
            f.write(json.dumps(_stable_snapshot("r2")) + "\n")

        hook = DriftDetectorHook(history_file=history_file, window=5)
        hook.before_run(RunStarted(run_id="r3", total_tasks=1))
        assert len(hook._history) == 2  # skipped the bad line

    # ── Hook isolation ───────────────────────────────────────────────────

    def test_broken_drift_detector_never_kills_run(self) -> None:
        """Hook exceptions must be swallowed by HookRegistry. Run continues."""
        registry = HookRegistry()
        # Invalid path will cause error during after_run persistence
        hook = DriftDetectorHook(history_file=Path("/nonexistent/path/drift.jsonl"))
        registry.register(hook)
        # Must not raise
        registry.fire("before_run", RunStarted(run_id="r1", total_tasks=1))
        registry.fire("after_run", RunCompleted(run_id="r1", summary=_FakeSummary()))

    # ── Config validation ────────────────────────────────────────────────

    def test_rejects_negative_window(self, tmp_path: Path) -> None:
        """Should raise VeridianConfigError for window < 1."""
        with pytest.raises(VeridianConfigError, match="window"):
            DriftDetectorHook(
                history_file=tmp_path / "h.jsonl", window=-1
            )

    def test_rejects_threshold_above_one(self, tmp_path: Path) -> None:
        """Should raise VeridianConfigError for threshold > 1.0."""
        with pytest.raises(VeridianConfigError, match="threshold"):
            DriftDetectorHook(
                history_file=tmp_path / "h.jsonl", threshold=1.5
            )

    # ── Report generation ────────────────────────────────────────────────

    def test_generates_markdown_report(
        self, tmp_path: Path
    ) -> None:
        """Should generate a readable drift_report.md."""
        history_file = tmp_path / "drift_history.jsonl"
        report_path = tmp_path / "drift_report.md"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file,
            window=5,
            threshold=0.15,
            report_path=report_path,
        )
        # Degraded run
        hook.before_run(RunStarted(run_id="r_bad", total_tasks=10))
        for i in range(6):
            hook.after_task(_make_task_completed(task_id=f"t_pass_{i}"))
        for i in range(4):
            hook.on_failure(_make_task_failed(task_id=f"t_fail_{i}"))
        summary = _FakeSummary(done_count=6, failed_count=4, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_bad", summary=summary))

        assert report_path.exists()
        content = report_path.read_text()
        assert "drift" in content.lower()
        assert "r_bad" in content

    def test_report_includes_recommended_actions(
        self, tmp_path: Path
    ) -> None:
        """Should include actionable recommendations in drift report."""
        history_file = tmp_path / "drift_history.jsonl"
        _write_history(
            history_file,
            [_stable_snapshot(f"r_{i}") for i in range(7)],
        )
        hook = DriftDetectorHook(
            history_file=history_file, window=5, threshold=0.15
        )
        # Degraded run
        hook.before_run(RunStarted(run_id="r_bad", total_tasks=10))
        for i in range(6):
            hook.after_task(_make_task_completed(task_id=f"t_{i}"))
        for i in range(4):
            hook.on_failure(_make_task_failed(task_id=f"t_fail_{i}"))
        summary = _FakeSummary(done_count=6, failed_count=4, total_tasks=10)
        hook.after_run(RunCompleted(run_id="r_bad", summary=summary))

        report = hook.last_report
        assert report is not None
        assert len(report.recommended_actions) > 0

    # ── First run (no history) ───────────────────────────────────────────

    def test_first_run_no_history_is_stable(
        self, hook: DriftDetectorHook
    ) -> None:
        """Should report stable when there's no baseline to compare against."""
        hook.before_run(RunStarted(run_id="r1", total_tasks=5))
        for i in range(5):
            hook.after_task(_make_task_completed(task_id=f"t_{i}"))
        summary = _FakeSummary(done_count=5, total_tasks=5)
        hook.after_run(RunCompleted(run_id="r1", summary=summary))

        report = hook.last_report
        assert report is not None
        assert report.overall_status == "stable"
        assert len(report.signals) == 0
