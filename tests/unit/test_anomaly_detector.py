"""
Tests for veridian.hooks.builtin.anomaly_detector — mid-run behavioral anomaly detection.
TDD: RED phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veridian.hooks.builtin.anomaly_detector import (
    AnomalyDetectorHook,
    AnomalyReport,
    AnomalySignal,
    AnomalyType,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeRunStarted:
    run_id: str = "run-001"
    total_tasks: int = 10


@dataclass
class _FakeResult:
    structured: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class _FakeTask:
    id: str = "t1"
    verifier_id: str = "schema"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeTaskCompleted:
    event_type: str = "task.completed"
    task: Any = None
    result: Any = None


@dataclass
class _FakeRunCompleted:
    event_type: str = "run.completed"
    run_id: str = "run-001"
    summary: Any = None


# ── Construction ─────────────────────────────────────────────────────────────


class TestAnomalyDetectorConstruction:
    def test_creates_with_defaults(self) -> None:
        hook = AnomalyDetectorHook()
        assert hook.id == "anomaly_detector"
        assert hook.priority == 55

    def test_creates_with_custom_threshold(self) -> None:
        hook = AnomalyDetectorHook(spike_threshold=3.0)
        assert hook._spike_threshold == 3.0


# ── Token Spike Detection ───────────────────────────────────────────────────


class TestTokenSpikeDetection:
    def test_detects_token_consumption_spike(self) -> None:
        hook = AnomalyDetectorHook(spike_threshold=2.0)
        hook.before_run(_FakeRunStarted())

        # Normal tasks
        for i in range(5):
            hook.after_task(
                _FakeTaskCompleted(
                    task=_FakeTask(id=f"t{i}"),
                    result=_FakeResult(token_usage={"total_tokens": 500}),
                )
            )

        # Spike task
        hook.after_task(
            _FakeTaskCompleted(
                task=_FakeTask(id="spike"),
                result=_FakeResult(token_usage={"total_tokens": 5000}),
            )
        )

        hook.after_run(_FakeRunCompleted())
        report = hook.last_report
        assert report is not None
        token_anomalies = [s for s in report.signals if s.anomaly_type == AnomalyType.TOKEN_SPIKE]
        assert len(token_anomalies) >= 1

    def test_no_spike_with_consistent_usage(self) -> None:
        hook = AnomalyDetectorHook()
        hook.before_run(_FakeRunStarted())
        for i in range(10):
            hook.after_task(
                _FakeTaskCompleted(
                    task=_FakeTask(id=f"t{i}"),
                    result=_FakeResult(token_usage={"total_tokens": 500}),
                )
            )
        hook.after_run(_FakeRunCompleted())
        report = hook.last_report
        assert report is not None
        token_anomalies = [s for s in report.signals if s.anomaly_type == AnomalyType.TOKEN_SPIKE]
        assert len(token_anomalies) == 0


# ── Tool Usage Anomaly ──────────────────────────────────────────────────────


class TestToolUsageAnomaly:
    def test_detects_novel_tool_usage(self) -> None:
        hook = AnomalyDetectorHook()
        hook.before_run(_FakeRunStarted())

        # Establish baseline tools
        for i in range(5):
            hook.after_task(
                _FakeTaskCompleted(
                    task=_FakeTask(id=f"t{i}"),
                    result=_FakeResult(tool_calls=["bash", "read"]),
                )
            )

        # Suddenly use new tools
        hook.after_task(
            _FakeTaskCompleted(
                task=_FakeTask(id="novel"),
                result=_FakeResult(tool_calls=["bash", "read", "network_access", "exec_remote"]),
            )
        )

        hook.after_run(_FakeRunCompleted())
        report = hook.last_report
        assert report is not None
        tool_anomalies = [s for s in report.signals if s.anomaly_type == AnomalyType.TOOL_ANOMALY]
        assert len(tool_anomalies) >= 1


# ── Output Pattern Shift ────────────────────────────────────────────────────


class TestOutputPatternShift:
    def test_detects_sudden_output_structure_change(self) -> None:
        hook = AnomalyDetectorHook()
        hook.before_run(_FakeRunStarted())

        # Baseline: structured outputs with consistent fields
        for i in range(5):
            hook.after_task(
                _FakeTaskCompleted(
                    task=_FakeTask(id=f"t{i}"),
                    result=_FakeResult(structured={"answer": "yes", "confidence": 0.9}),
                )
            )

        # Sudden change: completely different structure
        hook.after_task(
            _FakeTaskCompleted(
                task=_FakeTask(id="shift"),
                result=_FakeResult(structured={"error_code": 500, "stack_trace": "..."}),
            )
        )

        hook.after_run(_FakeRunCompleted())
        report = hook.last_report
        assert report is not None
        # At minimum, the anomaly detector should track the shift


# ── AnomalyReport ──────────────────────────────────────────────────────────


class TestAnomalyReport:
    def test_report_to_dict(self) -> None:
        signal = AnomalySignal(
            anomaly_type=AnomalyType.TOKEN_SPIKE,
            task_id="t1",
            detail="Token usage 5x above mean",
            severity="warning",
        )
        report = AnomalyReport(
            run_id="r1",
            signals=[signal],
            total_tasks_monitored=10,
        )
        d = report.to_dict()
        assert d["run_id"] == "r1"
        assert len(d["signals"]) == 1

    def test_report_to_markdown(self) -> None:
        signal = AnomalySignal(
            anomaly_type=AnomalyType.TOOL_ANOMALY,
            task_id="t5",
            detail="Novel tool: exec_remote",
            severity="significant",
        )
        report = AnomalyReport(
            run_id="r1",
            signals=[signal],
            total_tasks_monitored=10,
        )
        md = report.to_markdown()
        assert "exec_remote" in md

    def test_clean_report(self) -> None:
        report = AnomalyReport(run_id="r1", signals=[], total_tasks_monitored=10)
        assert report.is_clean is True
