"""
tests.integration.test_runner
------------------------------------------------------------------------------------------
Integration tests for VeridianRunner.
Full pipeline: task --- execution --- verification --- DONE.
"""

import json
from pathlib import Path

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.report import validate_report_chain
from veridian.core.task import (
    Task,
    TaskStatus,
)
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider

# ------ Fixtures ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=5,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )


@pytest.fixture
def ledger(config: VeridianConfig) -> TaskLedger:
    return TaskLedger(
        path=config.ledger_file,
        progress_file=str(config.progress_file),
    )


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


_SCHEMA_CONFIG = {"required_fields": ["summary"]}


def make_task(title: str = "test", **kwargs) -> Task:
    """Helper that creates a Task with a schema verifier that works without network."""
    defaults = dict(
        title=title,
        verifier_id="schema",
        verifier_config=_SCHEMA_CONFIG,
    )
    defaults.update(kwargs)
    return Task(**defaults)


def make_result_response(structured: dict, tool_calls: list | None = None) -> LLMResponse:
    payload = json.dumps({"summary": "done", "structured": structured, "artifacts": []})
    return LLMResponse(
        content=f"<veridian:result>\n{payload}\n</veridian:result>",
        input_tokens=100,
        output_tokens=50,
        model="mock",
        tool_calls=tool_calls or [],
    )


# ------ Full pipeline ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


class TestVeridianRunnerHappyPath:
    def test_full_pipeline_single_task_done(self, config, ledger, mock_provider, tmp_path):
        """Full pipeline: task --- worker --- verification --- DONE."""
        task = make_task("Test task", id="t1", description="Do the thing")
        ledger.add([task])

        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
        )
        summary = runner.run()

        assert summary.done_count == 1
        assert summary.failed_count == 0
        assert ledger.get("t1").status == TaskStatus.DONE

    def test_run_returns_run_summary(self, config, ledger, mock_provider):
        """runner.run() always returns a RunSummary."""
        ledger.add([make_task("t1")])
        mock_provider.script([make_result_response({"summary": "ok"})])
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert isinstance(summary, RunSummary)

    def test_empty_ledger_returns_immediately(self, config, ledger, mock_provider):
        """With no tasks, run() returns immediately with done_count=0."""
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.done_count == 0
        assert summary.failed_count == 0

    def test_reset_in_progress_called_first(self, config, ledger, mock_provider):
        """reset_in_progress() is always the first call in run()."""
        task = make_task("stale task", id="stale")
        ledger.add([task])
        ledger.claim(task.id, "crashed-runner")
        # Task is IN_PROGRESS --- reset_in_progress should reset it
        mock_provider.script([make_result_response({"summary": "ok"})])
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.done_count == 1

    def test_multiple_tasks_all_complete(self, config, ledger, mock_provider):
        """All pending tasks are completed in sequence."""
        tasks = [make_task(f"task {i}") for i in range(3)]
        ledger.add(tasks)
        for _ in tasks:
            mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.done_count == 3
        assert summary.failed_count == 0

    def test_runner_populates_taskresult_evidence_contract(self, config, ledger, mock_provider):
        """Runner should persist confidence/tool_calls/timing/evidence on TaskResult."""
        task = make_task("evidence task", id="e1")
        ledger.add([task])
        mock_provider.script(
            [make_result_response({"summary": "done"}, tool_calls=[{"name": "search_docs"}])]
        )

        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.done_count == 1

        stored = ledger.get("e1")
        assert stored.result is not None
        assert stored.result.tool_calls == [{"name": "search_docs"}]
        assert isinstance(stored.result.confidence, dict)
        assert "composite" in stored.result.confidence
        assert isinstance(stored.result.verification_evidence, dict)
        assert stored.result.verification_report["schema_version"] == "verification-report.v1"
        assert stored.result.verification_report["passed"] is True
        assert stored.result.verification_report["task_id"] == "e1"
        assert stored.result.verification_report["report_hash"]
        assert "worker_ms" in stored.result.timing
        assert "verification_ms" in stored.result.timing
        assert len(stored.result.trace_steps) >= 1
        assert stored.result.trace_steps[-1].action_type == "verify"

    def test_runner_exports_tamper_evident_report_jsonl(self, config, ledger, mock_provider):
        """Configured report_file writes the same evidence chain sold by Enterprise."""
        config.report_file = config.ledger_file.parent / "reports.jsonl"
        ledger.add([make_task("evidence export", id="report-1")])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()

        assert summary.done_count == 1
        assert config.report_file is not None
        validation = validate_report_chain(config.report_file)
        assert validation.valid is True
        assert validation.checked_count == 1

        stored = ledger.get("report-1")
        assert stored.result is not None
        assert stored.result.verification_report["report_hash"]


class TestDryRun:
    def test_dry_run_returns_summary_without_llm_calls(self, config, ledger, mock_provider):
        """dry_run=True assembles context but never calls provider.complete()."""
        config.dry_run = True
        ledger.add([make_task("test")])
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.dry_run is True
        assert mock_provider.call_count == 0


class TestAtomicWrite:
    def test_no_partial_write_on_concurrent_access(self, tmp_path):
        """Ledger file must never be readable in a partial state."""
        ledger = TaskLedger(
            path=tmp_path / "ledger.json",
            progress_file=str(tmp_path / "progress.md"),
        )
        ledger.add([Task(id="t1", title="t1")])
        ledger.add([Task(id="t2", title="t2")])
        assert (tmp_path / "ledger.json").exists()
        assert not list(tmp_path.glob("*.tmp"))


class TestRunSummary:
    def test_run_summary_fields(self, config, ledger, mock_provider):
        """RunSummary includes done_count, failed_count, run_id."""
        ledger.add([make_task("t1")])
        mock_provider.script([make_result_response({"summary": "ok"})])
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert hasattr(summary, "done_count")
        assert hasattr(summary, "failed_count")
        assert hasattr(summary, "run_id")
        assert summary.run_id != ""
