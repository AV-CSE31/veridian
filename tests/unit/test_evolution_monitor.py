"""
Tests for veridian.hooks.builtin.evolution_monitor
──────────────────────────────────────────────────
Evolution Safety Monitor — detects all 6 misevolution pathways.
TDD: RED phase — tests written before implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.hooks.builtin.evolution_monitor import (
    EvolutionMonitorHook,
    EvolutionSafetyReport,
    MisevolutionWarning,
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
class _FakeResult:
    structured: dict[str, Any] = None  # type: ignore[assignment]
    confidence: Any = None
    token_usage: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.structured is None:
            self.structured = {}
        if self.token_usage is None:
            self.token_usage = {}


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


class TestEvolutionMonitorConstruction:
    def test_creates_with_defaults(self) -> None:
        hook = EvolutionMonitorHook()
        assert hook.id == "evolution_monitor"
        assert hook.priority == 85

    def test_creates_with_custom_threshold(self) -> None:
        hook = EvolutionMonitorHook(safety_threshold=0.05)
        assert hook._safety_threshold == 0.05

    def test_rejects_negative_threshold(self) -> None:
        with pytest.raises(VeridianConfigError, match="threshold"):
            EvolutionMonitorHook(safety_threshold=-0.1)

    def test_rejects_threshold_over_one(self) -> None:
        with pytest.raises(VeridianConfigError, match="threshold"):
            EvolutionMonitorHook(safety_threshold=1.5)

    def test_creates_with_history_file(self, tmp_path: Path) -> None:
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        assert hook._history_file == tmp_path / "evo.jsonl"


# ── Pathway 1: Model Misevolution ───────────────────────────────────────────


class TestPathway1ModelMisevolution:
    def test_detects_safety_refusal_rate_drop(self, tmp_path: Path) -> None:
        """P1: safety refusal rate drops below baseline -> warning."""
        hook = EvolutionMonitorHook(
            history_file=tmp_path / "evo.jsonl",
            refusal_baseline=0.90,
            safety_threshold=0.10,
        )
        hook.before_run(_FakeRunStarted())

        # Simulate 10 tasks where only 5 had safety refusals (50% vs 90% baseline)
        for i in range(10):
            task = _FakeTask(id=f"t{i}", metadata={"safety_relevant": True})
            result = _FakeResult(
                structured={"refused_unsafe": i < 5}  # only first 5 refused
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway1 = [w for w in report.warnings if w.pathway == "model"]
        assert len(pathway1) >= 1
        assert pathway1[0].severity in ("warning", "significant")

    def test_no_warning_when_refusal_rate_high(self, tmp_path: Path) -> None:
        """P1: refusal rate above baseline -> no warning."""
        hook = EvolutionMonitorHook(
            history_file=tmp_path / "evo.jsonl",
            refusal_baseline=0.90,
        )
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}", metadata={"safety_relevant": True})
            result = _FakeResult(structured={"refused_unsafe": True})
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway1 = [w for w in report.warnings if w.pathway == "model"]
        assert len(pathway1) == 0


# ── Pathway 2: Memory Misevolution ──────────────────────────────────────────


class TestPathway2MemoryMisevolution:
    def test_detects_high_memory_bias_index(self, tmp_path: Path) -> None:
        """P2: high contradiction rate in memory updates -> warning."""
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(structured={"memory_contradictions": 5, "memory_updates": 10})
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway2 = [w for w in report.warnings if w.pathway == "memory"]
        assert len(pathway2) >= 1


# ── Pathway 3: Tool Misevolution ────────────────────────────────────────────


class TestPathway3ToolMisevolution:
    def test_detects_low_tool_safety_score(self, tmp_path: Path) -> None:
        """P3: tool safety pass rate drops -> warning."""
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(
                structured={"tool_safety_passed": i < 3}  # only 30% pass
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway3 = [w for w in report.warnings if w.pathway == "tool"]
        assert len(pathway3) >= 1


# ── Pathway 4: Workflow Misevolution ────────────────────────────────────────


class TestPathway4WorkflowMisevolution:
    def test_detects_verification_steps_skipped(self, tmp_path: Path) -> None:
        """P4: verification steps skipped -> warning."""
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(
                structured={"verification_steps_run": 1, "verification_steps_total": 3}
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway4 = [w for w in report.warnings if w.pathway == "workflow"]
        assert len(pathway4) >= 1


# ── Pathway 5: Environment Misevolution ─────────────────────────────────────


class TestPathway5EnvironmentMisevolution:
    def test_detects_anomalous_resource_access(self, tmp_path: Path) -> None:
        """P5: unexpected resource access patterns -> warning."""
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())

        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(structured={"resource_access_anomalies": 8})
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway5 = [w for w in report.warnings if w.pathway == "environment"]
        assert len(pathway5) >= 1


# ── Pathway 6: Evaluation Misevolution ──────────────────────────────────────


class TestPathway6EvaluationMisevolution:
    def test_detects_verifier_config_tampering(self, tmp_path: Path) -> None:
        """P6: verifier configuration changed mid-run -> warning."""
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())

        # Simulate config integrity check result
        for i in range(10):
            task = _FakeTask(id=f"t{i}")
            result = _FakeResult(
                structured={"verifier_config_intact": i < 7}  # 3 tampered
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))

        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        report = hook.last_report
        assert report is not None
        pathway6 = [w for w in report.warnings if w.pathway == "evaluation"]
        assert len(pathway6) >= 1


# ── Report ──────────────────────────────────────────────────────────────────


class TestEvolutionSafetyReport:
    def test_report_to_dict(self) -> None:
        warning = MisevolutionWarning(
            pathway="model",
            metric="safety_refusal_rate",
            baseline_value=0.95,
            current_value=0.50,
            z_score=-3.0,
            severity="significant",
            recommended_action="Check model version",
        )
        report = EvolutionSafetyReport(
            run_id="r1",
            timestamp="2026-03-30T00:00:00",
            warnings=[warning],
            overall_status="degraded",
        )
        d = report.to_dict()
        assert d["run_id"] == "r1"
        assert d["overall_status"] == "degraded"
        assert len(d["warnings"]) == 1

    def test_report_to_markdown(self) -> None:
        warning = MisevolutionWarning(
            pathway="tool",
            metric="tool_safety_score",
            baseline_value=0.90,
            current_value=0.30,
            z_score=-4.0,
            severity="significant",
            recommended_action="Review generated code",
        )
        report = EvolutionSafetyReport(
            run_id="r1",
            timestamp="2026-03-30T00:00:00",
            warnings=[warning],
            overall_status="degraded",
        )
        md = report.to_markdown()
        assert "tool" in md.lower()
        assert "degraded" in md.lower()

    def test_healthy_report_has_no_warnings(self) -> None:
        report = EvolutionSafetyReport(
            run_id="r1",
            timestamp="2026-03-30T00:00:00",
            warnings=[],
            overall_status="healthy",
        )
        assert report.overall_status == "healthy"
        assert len(report.warnings) == 0


# ── Persistence ─────────────────────────────────────────────────────────────


class TestEvolutionMonitorPersistence:
    def test_persists_metrics_to_jsonl(self, tmp_path: Path) -> None:
        history_file = tmp_path / "evo.jsonl"
        hook = EvolutionMonitorHook(history_file=history_file)
        hook.before_run(_FakeRunStarted())

        task = _FakeTask(id="t1", metadata={"safety_relevant": True})
        result = _FakeResult(structured={"refused_unsafe": True})
        hook.after_task(_FakeTaskCompleted(task=task, result=result))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))

        assert history_file.exists()
        lines = [line for line in history_file.read_text().strip().split("\n") if line.strip()]
        assert len(lines) >= 1
        data = json.loads(lines[-1])
        assert "run_id" in data

    def test_loads_history_from_jsonl(self, tmp_path: Path) -> None:
        history_file = tmp_path / "evo.jsonl"
        entry = {"run_id": "old-run", "timestamp": "2026-01-01T00:00:00", "pathway_metrics": {}}
        history_file.write_text(json.dumps(entry) + "\n")

        hook = EvolutionMonitorHook(history_file=history_file)
        hook.before_run(_FakeRunStarted())
        assert len(hook._history) >= 1

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        history_file = tmp_path / "evo.jsonl"
        hook = EvolutionMonitorHook(history_file=history_file)
        hook.before_run(_FakeRunStarted())
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        assert not list(tmp_path.glob("*.tmp"))

    def test_writes_report_file(self, tmp_path: Path) -> None:
        report_path = tmp_path / "evolution_report.md"
        hook = EvolutionMonitorHook(
            history_file=tmp_path / "evo.jsonl",
            report_path=report_path,
        )
        hook.before_run(_FakeRunStarted())
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        assert report_path.exists()


# ── Overall Status Logic ────────────────────────────────────────────────────


class TestOverallStatus:
    def test_healthy_when_no_warnings(self, tmp_path: Path) -> None:
        hook = EvolutionMonitorHook(history_file=tmp_path / "evo.jsonl")
        hook.before_run(_FakeRunStarted())
        for i in range(10):
            task = _FakeTask(id=f"t{i}", metadata={"safety_relevant": True})
            result = _FakeResult(structured={"refused_unsafe": True})
            hook.after_task(_FakeTaskCompleted(task=task, result=result))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        assert hook.last_report is not None
        assert hook.last_report.overall_status == "healthy"

    def test_degraded_on_multiple_pathway_warnings(self, tmp_path: Path) -> None:
        hook = EvolutionMonitorHook(
            history_file=tmp_path / "evo.jsonl",
            refusal_baseline=0.90,
        )
        hook.before_run(_FakeRunStarted())
        for i in range(10):
            task = _FakeTask(id=f"t{i}", metadata={"safety_relevant": True})
            result = _FakeResult(
                structured={
                    "refused_unsafe": False,  # P1 failure
                    "tool_safety_passed": False,  # P3 failure
                    "verification_steps_run": 0,
                    "verification_steps_total": 3,  # P4 failure
                }
            )
            hook.after_task(_FakeTaskCompleted(task=task, result=result))
        hook.after_run(_FakeRunCompleted(summary=_FakeSummary()))
        assert hook.last_report is not None
        assert hook.last_report.overall_status == "degraded"
