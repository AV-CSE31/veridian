from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.exceptions import VeridianError
from veridian.core.task import Task
from veridian.integrations.crewai import (
    CrewAdapterError,
    CrewKickoffError,
    CrewVerificationContract,
    CrewVersionWarning,
    VeridianCrew,
)
from veridian.integrations.langgraph import VerificationError
from veridian.integrations.sdk import resume_run, start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


@dataclass
class _StubOutput:
    description: str
    raw: Any


@dataclass
class _StubAgent:
    role: str


@dataclass
class _StubCrewTask:
    description: str
    agent: _StubAgent | None = None


class _StubCrew:
    def __init__(self, tasks: list[_StubOutput], final: Any = "done") -> None:
        self._tasks = tasks
        self._final = final
        self.task_callback: Callable[[Any], Any] | None = None

    def kickoff(self, inputs: dict[str, Any]) -> Any:
        for task_output in self._tasks:
            if self.task_callback is not None:
                self.task_callback(task_output)
        return self._final


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
        title="crewai-cert",
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return task


def test_hierarchical_manager_and_agent_metadata(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)
    crew = _StubCrew([_StubOutput(description="analyze", raw={"summary": "ok"})])
    crew.process = "hierarchical"  # type: ignore[attr-defined]
    crew.manager_agent = _StubAgent(role="manager")  # type: ignore[attr-defined]
    crew.tasks = [  # type: ignore[attr-defined]
        _StubCrewTask(description="analyze", agent=_StubAgent(role="analyst"))
    ]

    wrapped = VeridianCrew(crew=crew, sdk_context=ctx, task=task)
    wrapped.kickoff({})
    step = next(s for s in ctx.trace_steps if s.metadata.get("node_id") == "analyze")
    assert step.metadata.get("manager_role") == "manager"
    assert step.metadata.get("agent_role") == "analyst"


def test_contract_verification_failure_propagates(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)
    contract = CrewVerificationContract(
        verifiers={"draft": "schema"},
        verifier_configs={"draft": {"required_fields": ["summary"]}},
        on_failure="raise",
    )
    crew = _StubCrew([_StubOutput(description="draft", raw={"wrong": "field"})])
    with pytest.raises(VerificationError):
        VeridianCrew(crew=crew, sdk_context=ctx, task=task, contract=contract).kickoff({})


def test_error_mapping_for_runtime_and_attribute_failures(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)

    class _RuntimeErrorCrew:
        task_callback: Callable[[Any], Any] | None = None

        def kickoff(self, inputs: dict[str, Any]) -> Any:
            raise RuntimeError("runtime failure")

    class _AttrErrorCrew:
        task_callback: Callable[[Any], Any] | None = None

        def kickoff(self, inputs: dict[str, Any]) -> Any:
            raise AttributeError("incompatible crew")

    with pytest.raises(CrewKickoffError) as runtime_exc:
        VeridianCrew(crew=_RuntimeErrorCrew(), sdk_context=ctx, task=task).kickoff({})
    assert isinstance(runtime_exc.value, VeridianError)

    with pytest.raises(CrewAdapterError):
        VeridianCrew(crew=_AttrErrorCrew(), sdk_context=ctx, task=task).kickoff({})


def test_interrupt_resume_restores_trace_steps(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)

    class _InterruptibleCrew:
        task_callback: Callable[[Any], Any] | None = None

        def kickoff(self, inputs: dict[str, Any]) -> Any:
            if self.task_callback is not None:
                self.task_callback(_StubOutput(description="step1", raw={"summary": "done"}))
            raise RuntimeError("interrupted")

    with pytest.raises(CrewKickoffError):
        VeridianCrew(crew=_InterruptibleCrew(), sdk_context=ctx, task=task).kickoff({})

    resumed = resume_run(config=config, provider=provider, task_id=task.id, ledger=ledger)
    assert any(step.metadata.get("node_id") == "step1" for step in resumed.trace_steps)


def test_unsupported_version_warns(
    sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger],
) -> None:
    config, provider, ledger = sdk_env
    task = _seed_task(ledger)
    ctx = start_run(config=config, provider=provider, ledger=ledger)
    crew = _StubCrew([])
    crew.__version__ = "0.50.0"  # type: ignore[attr-defined]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        VeridianCrew(crew=crew, sdk_context=ctx, task=task).kickoff({})
        compat = [x for x in caught if issubclass(x.category, CrewVersionWarning)]
        assert len(compat) == 1
