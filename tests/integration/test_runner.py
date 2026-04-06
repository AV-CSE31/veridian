"""
tests.integration.test_runner
──────────────────────────────
Integration tests for VeridianRunner and ParallelRunner.
Full pipeline: task → execution → verification → DONE.
"""

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import (
    PRMBudget,
    PRMRunResult,
    PRMScore,
    Task,
    TaskResult,
    TaskStatus,
    TraceStep,
)
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider
from veridian.verify.base import PRMVerifier, VerifierRegistry
from veridian.verify.builtin.schema import SchemaVerifier

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


def make_result_response(structured: dict, tool_calls: list | None = None) -> LLMResponse:
    payload = json.dumps({"summary": "done", "structured": structured, "artifacts": []})
    return LLMResponse(
        content=f"<veridian:result>\n{payload}\n</veridian:result>",
        input_tokens=100,
        output_tokens=50,
        model="mock",
        tool_calls=tool_calls or [],
    )


class DeterministicPRMVerifier(PRMVerifier):
    """Test-only PRM verifier with configurable first/repair scores."""

    id: ClassVar[str] = "deterministic_prm"
    description: ClassVar[str] = "Deterministic PRM verifier used by integration tests."

    def __init__(
        self,
        first_score: float = 0.9,
        repaired_score: float | None = None,
        aggregate_confidence: float = 0.9,
        threshold: float = 0.72,
        forbidden_step_ids: list[str] | None = None,
        model_id: str = "deterministic_prm",
        version: str = "1",
    ) -> None:
        self.first_score = first_score
        self.repaired_score = repaired_score
        self.aggregate_confidence = aggregate_confidence
        self.threshold = threshold
        self.forbidden_step_ids = set(forbidden_step_ids or [])
        self.model_id = model_id
        self.version = version

    def score_steps(
        self,
        *,
        task_id: str,
        steps: list,
        context: dict[str, Any],
        budget: PRMBudget,
    ) -> PRMRunResult:
        _ = budget
        repair_attempts = int(context.get("repair_attempts", 0))
        score = self.first_score
        if repair_attempts > 0 and self.repaired_score is not None:
            score = self.repaired_score
        for step in steps:
            if getattr(step, "step_id", "") in self.forbidden_step_ids:
                raise RuntimeError(f"duplicate scored step detected: {step.step_id}")
        scored_steps = [
            PRMScore(
                step_id=getattr(step, "step_id", f"{task_id}_{idx}"),
                score=score,
                confidence=self.aggregate_confidence,
                model_id=self.model_id,
                version=self.version,
                failure_mode=None if score >= self.threshold else "score_below_threshold",
            )
            for idx, step in enumerate(steps, start=1)
        ]
        return PRMRunResult(
            passed=score >= self.threshold and self.aggregate_confidence >= 0.65,
            aggregate_score=score,
            aggregate_confidence=self.aggregate_confidence,
            threshold=self.threshold,
            scored_steps=scored_steps,
            policy_action="allow",
            repair_hint="Use concrete, deterministic evidence.",
            error=None if score >= self.threshold else "score_below_threshold",
        )


def make_prm_test_registry() -> VerifierRegistry:
    registry = VerifierRegistry()
    registry.register_many(SchemaVerifier, DeterministicPRMVerifier)
    return registry


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
        assert "worker_ms" in stored.result.timing
        assert "verification_ms" in stored.result.timing
        assert len(stored.result.trace_steps) >= 1
        assert stored.result.trace_steps[-1].action_type == "verify"


class TestRunnerHookLifecycleIntegration:
    def test_cross_run_consistency_executes_via_standard_lifecycle(
        self, config, ledger, mock_provider
    ):
        """CrossRunConsistencyHook must run through before_run/after_task lifecycle."""
        from veridian.hooks.builtin.cross_run_consistency import CrossRunConsistencyHook
        from veridian.hooks.registry import HookRegistry

        tasks = [
            make_task("task 1", id="t1"),
            make_task("task 2", id="t2"),
        ]
        ledger.add(tasks)
        mock_provider.script([make_result_response({"summary": "ok", "risk_level": "HIGH"})])
        mock_provider.script([make_result_response({"summary": "ok", "risk_level": "LOW"})])

        hook = CrossRunConsistencyHook(claim_fields=["risk_level"])
        hooks = HookRegistry()
        hooks.register(hook)

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            hooks=hooks,
        )
        summary = runner.run()

        assert summary.done_count == 2
        assert len(hook.conflicts) == 1
        assert hook.conflicts[0].field == "risk_level"
        assert hook.conflicts[0].severity == "critical"


class TestDryRun:
    def test_dry_run_returns_summary_without_llm_calls(self, config, ledger, mock_provider):
        """dry_run=True assembles context but never calls provider.complete()."""
        config.dry_run = True
        ledger.add([make_task("test")])
        runner = VeridianRunner(ledger=ledger, provider=mock_provider, config=config)
        summary = runner.run()
        assert summary.dry_run is True
        assert mock_provider.call_count == 0


class TestPRMRunnerLifecycle:
    def test_runner_blocks_when_prm_policy_blocks(self, config, ledger, mock_provider):
        task = make_task(
            "prm block",
            id="prm-block",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {"first_score": 0.2, "aggregate_confidence": 0.95},
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": True,
                }
            },
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.failed_count == 1
        stored = ledger.get("prm-block")
        assert stored.status == TaskStatus.FAILED
        assert stored.result is not None
        assert stored.result.prm_result is not None
        assert stored.result.prm_result.policy_action == "block"
        assert stored.result.confidence is not None
        assert "prm_score" in stored.result.confidence

    def test_runner_retries_once_when_prm_requests_repair(self, config, ledger, mock_provider):
        task = make_task(
            "prm repair",
            id="prm-repair",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {
                        "first_score": 0.2,
                        "repaired_score": 0.95,
                        "aggregate_confidence": 0.9,
                    },
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "retry_with_repair",
                    "max_repairs": 1,
                    "strict_replay": True,
                }
            },
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "first"})])
        mock_provider.script([make_result_response({"summary": "second"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.done_count == 1
        assert mock_provider.call_count == 2
        stored = ledger.get("prm-repair")
        assert stored.status == TaskStatus.DONE
        assert stored.result is not None
        assert stored.result.prm_result is not None
        assert stored.result.prm_result.policy_action == "allow"
        assert any(
            step.action_type == "plan" and "[PRM_REPAIR_ATTEMPT]" in step.content
            for step in stored.result.trace_steps
        )

    def test_runner_blocks_on_strict_replay_snapshot_mismatch(self, config, ledger, mock_provider):
        task = make_task(
            "prm replay mismatch",
            id="prm-replay-mismatch",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {"first_score": 0.9, "aggregate_confidence": 0.9},
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": True,
                }
            },
            result=TaskResult(
                raw_output="checkpointed",
                structured={"summary": "done"},
                extras={
                    "prm_checkpoint": {
                        "replay_snapshot": {
                            "model_id": "different-model",
                            "version": "99",
                            "prompt_hash": "incompatible",
                        }
                    }
                },
            ),
        )
        ledger.add([task])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.failed_count == 1
        assert mock_provider.call_count == 0
        stored = ledger.get("prm-replay-mismatch")
        assert stored.status == TaskStatus.FAILED
        assert stored.last_error is not None
        assert "replay incompatible" in stored.last_error.lower()

    def test_runner_skips_rescoring_already_scored_step_ids_after_resume(
        self, config, ledger, mock_provider
    ):
        task = make_task(
            "prm replay checkpoint",
            id="prm-replay-checkpoint",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {
                        "first_score": 0.95,
                        "aggregate_confidence": 0.9,
                        "forbidden_step_ids": ["a1_1_turn_1"],
                    },
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": False,
                }
            },
            result=TaskResult(
                raw_output="checkpointed",
                structured={"summary": "done"},
                trace_steps=[
                    TraceStep(
                        step_id="a1_1_turn_1",
                        role="assistant",
                        action_type="reason",
                        content="done",
                        timestamp_ms=1,
                    )
                ],
                prm_result=PRMRunResult(
                    passed=True,
                    aggregate_score=0.9,
                    aggregate_confidence=0.9,
                    threshold=0.8,
                    scored_steps=[
                        PRMScore(
                            step_id="a1_1_turn_1",
                            score=0.9,
                            confidence=0.9,
                            model_id="deterministic_prm",
                            version="1",
                            failure_mode=None,
                        )
                    ],
                    policy_action="allow",
                ),
                extras={"prm_checkpoint": {"repair_attempts": 0, "ready_to_finalize": False}},
            ),
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.done_count == 1
        stored = ledger.get("prm-replay-checkpoint")
        assert stored.status == TaskStatus.DONE
        assert stored.result is not None
        checkpoint = stored.result.extras.get("prm_checkpoint", {})
        assert checkpoint.get("prm_scored_until_step_id")
        assert checkpoint.get("activity_invocation_ids")

    def test_runner_finalizes_from_checkpoint_without_worker_rerun(
        self, config, ledger, mock_provider
    ):
        task = make_task(
            "prm finalize checkpoint",
            id="prm-finalize-checkpoint",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "strict_replay": False,
                }
            },
            result=TaskResult(
                raw_output="checkpointed",
                structured={"summary": "done"},
                prm_result=PRMRunResult(
                    passed=True,
                    aggregate_score=0.92,
                    aggregate_confidence=0.91,
                    threshold=0.72,
                    scored_steps=[],
                    policy_action="allow",
                ),
                extras={
                    "prm_checkpoint": {
                        "ready_to_finalize": True,
                        "last_verification_passed": True,
                        "last_error": "",
                        "last_policy_action": "allow",
                        "repair_attempts": 0,
                    }
                },
            ),
        )
        ledger.add([task])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.done_count == 1
        assert mock_provider.call_count == 0
        stored = ledger.get("prm-finalize-checkpoint")
        assert stored.status == TaskStatus.DONE

    def test_runner_blocks_when_prm_budget_is_exceeded(self, config, ledger, mock_provider):
        task = make_task(
            "prm budget",
            id="prm-budget",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {"first_score": 0.95, "aggregate_confidence": 0.9},
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": True,
                    "budget": {"max_steps_per_call": 1},
                }
            },
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()

        assert summary.failed_count == 1
        stored = ledger.get("prm-budget")
        assert stored.status == TaskStatus.FAILED
        assert stored.result is not None
        assert stored.result.prm_result is not None
        assert "budget exceeded" in (stored.result.prm_result.error or "")

    def test_runner_emits_prm_trace_events_when_trace_enabled(
        self, config, ledger, mock_provider, tmp_path
    ):
        trace_file = tmp_path / "prm_trace.jsonl"
        config.trace_file = str(trace_file)
        task = make_task(
            "prm tracing",
            id="prm-tracing",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {"first_score": 0.95, "aggregate_confidence": 0.95},
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "strict_replay": True,
                }
            },
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = VeridianRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = runner.run()
        assert summary.done_count == 1
        assert trace_file.exists()

        events = [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]
        event_types = {e.get("event_type") for e in events}
        assert "veridian.prm.score_steps" in event_types
        assert "veridian.prm.policy_decision" in event_types


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

    @pytest.mark.asyncio
    async def test_parallel_runner_applies_prm_policy_actions(self, config, ledger, mock_provider):
        from veridian.loop.parallel_runner import ParallelRunner

        config.max_parallel = 2
        task = make_task(
            "parallel prm block",
            id="par-prm-block",
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "deterministic_prm",
                    "verifier_config": {"first_score": 0.1, "aggregate_confidence": 0.95},
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": True,
                }
            },
        )
        ledger.add([task])
        mock_provider.script([make_result_response({"summary": "done"})])

        runner = ParallelRunner(
            ledger=ledger,
            provider=mock_provider,
            config=config,
            verifier_registry=make_prm_test_registry(),
        )
        summary = await runner.run_async()

        assert summary.failed_count == 1
        stored = ledger.get("par-prm-block")
        assert stored.status == TaskStatus.FAILED
        assert stored.result is not None
        assert stored.result.prm_result is not None
        assert stored.result.prm_result.policy_action == "block"

    @pytest.mark.asyncio
    async def test_parallel_runner_fires_run_lifecycle_hooks(self, config, ledger, mock_provider):
        from veridian.hooks.base import BaseHook
        from veridian.hooks.registry import HookRegistry
        from veridian.loop.parallel_runner import ParallelRunner

        class CaptureHook(BaseHook):
            id: ClassVar[str] = "capture"

            def __init__(self) -> None:
                self.before_run_count = 0
                self.after_run_count = 0
                self.last_before_run_id = ""
                self.last_after_run_id = ""

            def before_run(self, event) -> None:
                self.before_run_count += 1
                self.last_before_run_id = event.run_id

            def after_run(self, event) -> None:
                self.after_run_count += 1
                self.last_after_run_id = event.run_id

        config.max_parallel = 2
        ledger.add([make_task("task 1"), make_task("task 2")])
        mock_provider.script([make_result_response({"summary": "done"})])
        mock_provider.script([make_result_response({"summary": "done"})])

        hook = CaptureHook()
        hooks = HookRegistry()
        hooks.register(hook)
        runner = ParallelRunner(ledger=ledger, provider=mock_provider, config=config, hooks=hooks)
        summary = await runner.run_async()

        assert summary.done_count == 2
        assert hook.before_run_count == 1
        assert hook.after_run_count == 1
        assert hook.last_before_run_id == summary.run_id
        assert hook.last_after_run_id == summary.run_id

    @pytest.mark.asyncio
    async def test_parallel_runner_aggregates_per_task_summaries(
        self, config, ledger, mock_provider, monkeypatch
    ):
        from veridian.loop.parallel_runner import ParallelRunner

        config.max_parallel = 4
        tasks = [make_task(f"task {i}") for i in range(5)]
        ledger.add(tasks)

        runner = ParallelRunner(ledger=ledger, provider=mock_provider, config=config)

        def fake_single_task(*_args):
            return RunSummary(done_count=1)

        monkeypatch.setattr(runner, "_run_single_task", fake_single_task)
        summary = await runner.run_async()
        assert summary.done_count == 5
        assert summary.failed_count == 0
