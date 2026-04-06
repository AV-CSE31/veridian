from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.integrations.langgraph import (
    LangGraphAdapterError,
    LangGraphCompatibilityWarning,
    VeridianLangGraph,
    VerificationContract,
    VerificationError,
)
from veridian.integrations.sdk import resume_run, start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


class _StubGraph:
    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self._script = script

    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        for node_id, output in self._script:
            yield {node_id: output}


class _ConditionalGraph:
    def __init__(self, routes: dict[str, list[tuple[str, Any]]]) -> None:
        self._routes = routes

    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        key = state.get("route", "default") if isinstance(state, dict) else "default"
        for node_id, output in self._routes.get(key, self._routes.get("default", [])):
            yield {node_id: output}


class _InterruptibleGraph:
    def __init__(self) -> None:
        self._interrupted = False

    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        if not self._interrupted:
            self._interrupted = True
            yield {"step1": {"summary": "s1"}}
            raise KeyboardInterrupt("pause")
        yield {"step2": {"summary": "s2"}}


class _ErrorGraph:
    def stream(self, state: Any) -> Iterator[dict[str, Any]]:
        raise RuntimeError("framework error")


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


def _seed_task(ledger: TaskLedger) -> Task:
    task = Task(
        title="langgraph-cert",
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return task


def test_checkpoint_and_resume_semantics(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)

    graph = _InterruptibleGraph()
    wrapped = VeridianLangGraph(graph=graph, sdk_context=ctx, task=task)
    with pytest.raises(KeyboardInterrupt):
        wrapped.invoke({})

    resumed = resume_run(config=config, provider=provider, task_id=task.id)
    wrapped_after = VeridianLangGraph(graph=graph, sdk_context=resumed, task=task)
    result = wrapped_after.invoke({})
    assert result == {"summary": "s2"}
    assert any(step.metadata.get("node_id") == "step1" for step in resumed.trace_steps)


def test_conditional_routing_and_verified_edge(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)

    contract = VerificationContract(
        verifiers={"validated": "schema"},
        verifier_configs={"validated": {"required_fields": ["summary"]}},
        on_failure="raise",
    )
    graph = _ConditionalGraph(
        routes={
            "ok": [("validated", {"summary": "good"})],
            "bad": [("validated", {"wrong": "field"})],
        }
    )
    wrapped = VeridianLangGraph(graph=graph, sdk_context=ctx, task=task, contract=contract)
    assert wrapped.invoke({"route": "ok"}) == {"summary": "good"}
    with pytest.raises(VerificationError):
        wrapped.invoke({"route": "bad"})


def test_framework_error_wrapped(sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)
    wrapped = VeridianLangGraph(graph=_ErrorGraph(), sdk_context=ctx, task=task)
    with pytest.raises(LangGraphAdapterError):
        wrapped.invoke({})


def test_version_compatibility_warning_emitted(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
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
        compat = [x for x in caught if issubclass(x.category, LangGraphCompatibilityWarning)]
        assert len(compat) == 1
