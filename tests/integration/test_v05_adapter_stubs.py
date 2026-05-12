"""
tests.integration.test_v05_adapter_stubs
────────────────────────────────────────
Preview-level certification tests for the v0.5 adapter stubs.

Each adapter is exercised against a hermetic stub of the upstream framework
so CI does not need the third-party SDKs installed. The goal is to lock in
the verification boundary contract: the upstream framework produces an
output, Veridian's verifier admits or rejects it, and the result surfaces
through the adapter's framework-shaped return type.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.integrations.inspect_ai import (
    export_outcome,
    export_outcomes,
)
from veridian.integrations.mastra import (
    MastraPreviewWarning,
    MastraStep,
    VeridianMastraSidecar,
)
from veridian.integrations.openai_agents import (
    OpenAIAgentsPreviewWarning,
    VeridianOpenAIAgentsGuardrail,
)
from veridian.integrations.pydantic_ai import (
    PydanticAIAdapterError,
    PydanticAIPreviewWarning,
    VeridianPydanticAI,
)
from veridian.integrations.sdk import VerificationOutcome, start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


@pytest.fixture
def env(tmp_path: Path) -> tuple[VeridianConfig, MockProvider, TaskLedger]:
    config = VeridianConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )
    provider = MockProvider()
    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    return config, provider, ledger


def _seed_task(ledger: TaskLedger, title: str = "t") -> Task:
    task = Task(
        title=title,
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return task


def _ctx(env: tuple[VeridianConfig, MockProvider, TaskLedger]) -> Any:
    config, provider, ledger = env
    return start_run(config=config, provider=provider, ledger=ledger)


# ── Pydantic AI ─────────────────────────────────────────────────────────────


class _PydanticAgentStub:
    def __init__(self, output: Any) -> None:
        self._output = output

    def run_sync(self, prompt: str, **kwargs: Any) -> Any:
        class _Result:
            data = self._output

        return _Result()


class TestPydanticAIAdapter:
    def test_passes_when_verifier_accepts(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)
        agent = _PydanticAgentStub({"summary": "ok"})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", PydanticAIPreviewWarning)
            wrapper = VeridianPydanticAI(agent=agent, sdk_context=ctx, task=task)
        outcome = wrapper.run_sync("hello")
        assert outcome.passed
        assert outcome.verifier_id == "schema"

    def test_fails_when_verifier_rejects(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)
        agent = _PydanticAgentStub({"wrong": "shape"})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", PydanticAIPreviewWarning)
            wrapper = VeridianPydanticAI(agent=agent, sdk_context=ctx, task=task)
        outcome = wrapper.run_sync("hello")
        assert not outcome.passed
        assert outcome.error

    def test_emits_preview_warning(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)
        agent = _PydanticAgentStub({"summary": "ok"})

        with pytest.warns(PydanticAIPreviewWarning, match="preview"):
            VeridianPydanticAI(agent=agent, sdk_context=ctx, task=task)

    def test_missing_run_sync_raises(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", PydanticAIPreviewWarning)
            wrapper = VeridianPydanticAI(agent=object(), sdk_context=ctx, task=task)
        with pytest.raises(PydanticAIAdapterError, match="run_sync"):
            wrapper.run_sync("hi")


# ── Mastra ──────────────────────────────────────────────────────────────────


class TestMastraSidecar:
    def test_verify_step_accepts_good_output(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MastraPreviewWarning)
            sidecar = VeridianMastraSidecar(sdk_context=ctx, task=task)
        outcome = sidecar.verify_step(MastraStep(step_id="s1", output={"summary": "ok"}))
        assert outcome.passed

    def test_verify_stream_processes_each_step(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MastraPreviewWarning)
            sidecar = VeridianMastraSidecar(sdk_context=ctx, task=task)

        steps = [
            MastraStep(step_id="s1", output={"summary": "ok"}),
            MastraStep(step_id="s2", output={"missing": "field"}),
        ]
        outcomes = list(sidecar.verify_stream(steps))
        assert len(outcomes) == 2
        assert outcomes[0].passed
        assert not outcomes[1].passed


# ── OpenAI Agents SDK guardrail ─────────────────────────────────────────────


class TestOpenAIAgentsGuardrail:
    def test_decision_allow_on_passing_verifier(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OpenAIAgentsPreviewWarning)
            guardrail = VeridianOpenAIAgentsGuardrail(sdk_context=ctx, task=task)
        decision = guardrail.check({"summary": "ok"})
        assert decision.allow
        assert decision.reason == ""
        assert decision.outcome.passed

    def test_decision_block_carries_reason(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        _, _, ledger = env
        task = _seed_task(ledger)
        ctx = _ctx(env)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OpenAIAgentsPreviewWarning)
            guardrail = VeridianOpenAIAgentsGuardrail(sdk_context=ctx, task=task)
        decision = guardrail.check({"nope": True})
        assert not decision.allow
        assert decision.reason
        assert not decision.outcome.passed


# ── Inspect AI evidence export ──────────────────────────────────────────────


def _mk_outcome(passed: bool = True) -> VerificationOutcome:
    return VerificationOutcome(
        passed=passed,
        error=None if passed else "missing field",
        evidence={"matched": True} if passed else {"matched": False},
        score=1.0 if passed else 0.0,
        verifier_id="schema",
    )


class TestInspectAIExport:
    def test_export_outcome_shape(self) -> None:
        task = Task(id="task-1", title="t", description="d", verifier_id="schema")
        sample = export_outcome(task=task, outcome=_mk_outcome())

        assert sample["id"] == "task-1"
        assert sample["input"]["title"] == "t"
        assert "schema" in sample["scores"]
        assert sample["scores"]["schema"]["value"] == "C"
        assert sample["metadata"]["verifier_id"] == "schema"

    def test_export_outcome_failure_uses_incorrect_value(self) -> None:
        task = Task(id="task-2", title="t", description="d", verifier_id="schema")
        sample = export_outcome(task=task, outcome=_mk_outcome(passed=False))
        assert sample["scores"]["schema"]["value"] == "I"
        assert sample["scores"]["schema"]["explanation"] == "missing field"

    def test_export_outcomes_batches(self) -> None:
        task = Task(id="batch", title="t", description="d", verifier_id="schema")
        pairs = [(task, _mk_outcome()), (task, _mk_outcome(passed=False))]
        samples = export_outcomes(pairs)
        assert len(samples) == 2
        assert samples[0]["scores"]["schema"]["value"] == "C"
        assert samples[1]["scores"]["schema"]["value"] == "I"
