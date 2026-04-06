"""
tests.integration.test_langgraph_adapter
─────────────────────────────────────────
RV3-007: end-to-end coverage using a hermetic LangGraph stub so no live
langgraph dependency is required in CI.

Stub exposes ``stream(state)`` yielding ``{node_id: output}`` updates, which
matches the real LangGraph CompiledGraph interface.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.exceptions import VeridianError
from veridian.core.task import Task
from veridian.integrations.langgraph import (
    LangGraphAdapterError,
    LangGraphCompatibilityWarning,
    VeridianLangGraph,
    VerificationContract,
    VerificationError,
)
from veridian.integrations.sdk import replay_run, resume_run, start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


class _StubGraph:
    """Minimal duck-typed LangGraph CompiledGraph for hermetic testing.

    The stub yields ``{node_id: output}`` dicts, exactly like a real
    LangGraph ``CompiledGraph.stream()`` returns.
    """

    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self._script = script

    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        for node_id, output in self._script:
            yield {node_id: output}


class _ErrorStubGraph:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        raise self._error


@pytest.fixture
def sdk_env(tmp_path: Path) -> tuple[VeridianConfig, MockProvider, TaskLedger]:
    config = VeridianConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        activity_journal_enabled=True,
    )
    provider = MockProvider()
    provider.model = "mock/v1"  # type: ignore[attr-defined]
    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    return config, provider, ledger


def _seed_task(ledger: TaskLedger, title: str = "lg") -> Task:
    task = Task(
        title=title,
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return task


class TestVeridianLangGraphBasicFlow:
    def test_invoke_records_trace_steps_per_node(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        stub = _StubGraph(
            [
                ("search", {"summary": "found sources"}),
                ("draft", {"summary": "drafted"}),
                ("refine", {"summary": "polished"}),
            ]
        )
        wrapped = VeridianLangGraph(graph=stub, sdk_context=ctx, task=task)
        final = wrapped.invoke({"query": "x"})

        assert len(ctx.trace_steps) == 3
        assert [s.metadata["node_id"] for s in ctx.trace_steps] == [
            "search",
            "draft",
            "refine",
        ]
        assert final == {"summary": "polished"}

    def test_checkpoint_persists_after_each_node(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        stub = _StubGraph([("a", {"summary": "A"}), ("b", {"summary": "B"})])
        wrapped = VeridianLangGraph(graph=stub, sdk_context=ctx, task=task)
        wrapped.invoke({})

        stored = ledger.get(task.id)
        assert stored.result is not None
        assert len(stored.result.trace_steps) >= 2
        node_ids = {s.metadata.get("node_id") for s in stored.result.trace_steps}
        assert {"a", "b"}.issubset(node_ids)


class TestVerifiedEdgeContract:
    def test_contract_raises_on_verifier_failure(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        contract = VerificationContract(
            verifiers={"draft": "schema"},
            verifier_configs={"draft": {"required_fields": ["summary"]}},
            on_failure="raise",
        )
        stub = _StubGraph([("draft", {"wrong_field": "missing summary"})])
        wrapped = VeridianLangGraph(graph=stub, sdk_context=ctx, task=task, contract=contract)

        with pytest.raises(VerificationError) as exc_info:
            wrapped.invoke({})
        assert exc_info.value.node_id == "draft"
        assert exc_info.value.verifier_id == "schema"

    def test_contract_passes_when_verifier_accepts(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        contract = VerificationContract(
            verifiers={"draft": "schema"},
            verifier_configs={"draft": {"required_fields": ["summary"]}},
        )
        stub = _StubGraph([("draft", {"summary": "valid draft"})])
        wrapped = VeridianLangGraph(graph=stub, sdk_context=ctx, task=task, contract=contract)
        result = wrapped.invoke({})
        assert result == {"summary": "valid draft"}


class TestResumeAndReplay:
    def test_resume_run_restores_trace_steps_and_journal(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx_a = start_run(config=config, provider=provider, ledger=ledger)
        stub = _StubGraph([("a", {"summary": "A"}), ("b", {"summary": "B"})])
        VeridianLangGraph(graph=stub, sdk_context=ctx_a, task=task).invoke({})

        # Simulate restart
        ctx_b = resume_run(config=config, provider=provider, task_id=task.id)
        assert len(ctx_b.trace_steps) >= 2
        assert any(s.metadata.get("node_id") == "a" for s in ctx_b.trace_steps)

    def test_replay_run_returns_report_with_snapshot(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        stub = _StubGraph([("a", {"summary": "A"})])
        VeridianLangGraph(graph=stub, sdk_context=ctx, task=task).invoke({})

        report = replay_run(ctx, task.id)
        assert report.task_id == task.id
        assert report.snapshot.get("model_id") == "mock/v1"
        assert report.replay_incompatible_reason is None


class TestErrorMapping:
    def test_framework_error_is_wrapped_as_langgraph_adapter_error(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        wrapped = VeridianLangGraph(
            graph=_ErrorStubGraph(RuntimeError("boom")),
            sdk_context=ctx,
            task=task,
        )
        with pytest.raises(LangGraphAdapterError) as exc_info:
            wrapped.invoke({})
        assert isinstance(exc_info.value, VeridianError)
        assert "boom" in str(exc_info.value)

    def test_veridian_errors_are_not_double_wrapped(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        original = LangGraphAdapterError("already wrapped")
        wrapped = VeridianLangGraph(
            graph=_ErrorStubGraph(original),
            sdk_context=ctx,
            task=task,
        )
        with pytest.raises(LangGraphAdapterError) as exc_info:
            wrapped.invoke({})
        assert exc_info.value is original


class TestVersionCompatibility:
    def test_check_compatibility_warns_for_unsupported_version(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        wrapped = VeridianLangGraph(graph=_StubGraph([]), sdk_context=ctx, task=task)
        mock_lg = MagicMock()
        mock_lg.__version__ = "0.1.0"
        with (
            patch.dict("sys.modules", {"langgraph": mock_lg}),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            wrapped._check_compatibility()
            compat = [
                item for item in caught if issubclass(item.category, LangGraphCompatibilityWarning)
            ]
            assert len(compat) == 1

    def test_check_compatibility_silent_for_supported_version(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        wrapped = VeridianLangGraph(graph=_StubGraph([]), sdk_context=ctx, task=task)
        mock_lg = MagicMock()
        mock_lg.__version__ = "0.2.99"
        with (
            patch.dict("sys.modules", {"langgraph": mock_lg}),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            wrapped._check_compatibility()
            compat = [
                item for item in caught if issubclass(item.category, LangGraphCompatibilityWarning)
            ]
            assert not compat
