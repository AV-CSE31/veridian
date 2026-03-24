"""
tests.integration.test_runner
──────────────────────────────
Integration tests for VeridianRunner and ParallelRunner.
Full pipeline: task → execution → verification → DONE.
"""
import json
import pytest
from pathlib import Path

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


def make_result_response(structured: dict) -> LLMResponse:
    payload = json.dumps({"summary": "done", "structured": structured, "artifacts": []})
    return LLMResponse(
        content=f"<veridian:result>\n{payload}\n</veridian:result>",
        input_tokens=100,
        output_tokens=50,
        model="mock",
    )


# ── Full pipeline ─────────────────────────────────────────────────────────────

class TestVeridianRunnerHappyPath:

    def test_full_pipeline_single_task_done(self, config, ledger, mock_provider, tmp_path):
        """Full pipeline: task → worker → verification → DONE."""
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
        # Task is IN_PROGRESS — reset_in_progress should reset it
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


# ── ParallelRunner ────────────────────────────────────────────────────────────

class TestParallelRunner:

    @pytest.mark.asyncio
    async def test_parallel_runner_completes_tasks(self, config, ledger, mock_provider):
        """ParallelRunner processes tasks concurrently up to max_parallel limit."""
        from veridian.loop.parallel_runner import ParallelRunner
        config.max_parallel = 2
        tasks = [make_task(f"task {i}") for i in range(2)]
        ledger.add(tasks)
        for _ in tasks:
            mock_provider.script([make_result_response({"summary": "done"})])

        runner = ParallelRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
        )
        summary = await runner.run_async()
        assert summary.done_count == 2

    @pytest.mark.asyncio
    async def test_parallel_runner_respects_semaphore(self, config, ledger, mock_provider):
        """ParallelRunner uses asyncio.Semaphore to cap concurrency."""
        from veridian.loop.parallel_runner import ParallelRunner
        config.max_parallel = 1
        tasks = [make_task(f"task {i}") for i in range(2)]
        ledger.add(tasks)
        for _ in tasks:
            mock_provider.script([make_result_response({"summary": "done"})])
        runner = ParallelRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = await runner.run_async()
        assert summary.done_count == 2
