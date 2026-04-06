"""
tests.integration.test_crewai_adapter
──────────────────────────────────────
RV3-008: end-to-end coverage using a hermetic CrewAI stub so no live crewai
dependency is required in CI.

Stub exposes ``kickoff(inputs)`` and a ``task_callback`` attribute that the
adapter wires into for per-task verification and checkpointing.
"""

from __future__ import annotations

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
from veridian.integrations.sdk import replay_run, start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


@dataclass
class _StubTaskOutput:
    """Minimal stand-in for crewai.TaskOutput used inside task_callback."""

    description: str
    raw: Any


class _StubCrew:
    """Hermetic CrewAI Crew stand-in.

    Fires ``task_callback`` per scripted task, then returns a final result.
    Matches the shape of the real ``Crew`` class closely enough for the
    adapter to wire into.
    """

    def __init__(self, tasks: list[_StubTaskOutput], final: Any = "crew final output") -> None:
        self._tasks = tasks
        self._final = final
        self.task_callback: Callable[[Any], Any] | None = None

    def kickoff(self, inputs: dict[str, Any]) -> Any:
        for task_output in self._tasks:
            if self.task_callback is not None:
                self.task_callback(task_output)
        return self._final


@dataclass
class _StubAgent:
    role: str


@dataclass
class _StubCrewTask:
    description: str
    agent: _StubAgent | None = None


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
        title="crew_run",
        verifier_id="schema",
        verifier_config={"required_fields": ["summary"]},
    )
    ledger.add([task])
    return task


class TestVeridianCrewRecording:
    def test_kickoff_records_step_per_crew_task(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        crew = _StubCrew(
            tasks=[
                _StubTaskOutput(description="research", raw={"summary": "found facts"}),
                _StubTaskOutput(description="draft", raw={"summary": "first pass"}),
                _StubTaskOutput(description="edit", raw={"summary": "polished"}),
            ]
        )
        wrapped = VeridianCrew(crew=crew, sdk_context=ctx, task=task)
        result = wrapped.kickoff({"topic": "test"})

        assert result == "crew final output"
        step_node_ids = [s.metadata["node_id"] for s in ctx.trace_steps]
        assert step_node_ids[:3] == ["research", "draft", "edit"]
        assert all(s.metadata["framework"] == "crewai" for s in ctx.trace_steps)

    def test_checkpoint_persists_after_each_crew_task(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        crew = _StubCrew(
            tasks=[
                _StubTaskOutput(description="a", raw={"summary": "A"}),
                _StubTaskOutput(description="b", raw={"summary": "B"}),
            ]
        )
        VeridianCrew(crew=crew, sdk_context=ctx, task=task).kickoff({})

        stored = ledger.get(task.id)
        assert stored.result is not None
        assert any(s.metadata.get("node_id") == "a" for s in stored.result.trace_steps)
        assert any(s.metadata.get("node_id") == "b" for s in stored.result.trace_steps)


class TestCrewContract:
    def test_contract_blocks_invalid_output(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        contract = CrewVerificationContract(
            verifiers={"draft": "schema"},
            verifier_configs={"draft": {"required_fields": ["summary"]}},
            on_failure="raise",
        )
        crew = _StubCrew(tasks=[_StubTaskOutput(description="draft", raw={"wrong_field": "oops"})])
        wrapped = VeridianCrew(crew=crew, sdk_context=ctx, task=task, contract=contract)
        with pytest.raises(VerificationError):
            wrapped.kickoff({})

    def test_contract_passes_valid_output_and_demonstrates_repair_path(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        """RV3-008 acceptance: example flow includes policy block + repair.
        We simulate: first call blocks, operator repairs the task output, a
        second kickoff passes. This matches the `on_failure='raise'` loop
        where the adapter user catches the error, fixes, retries."""
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        contract = CrewVerificationContract(
            verifiers={"draft": "schema"},
            verifier_configs={"draft": {"required_fields": ["summary"]}},
            on_failure="raise",
        )

        # Round 1: fails
        bad_crew = _StubCrew(tasks=[_StubTaskOutput(description="draft", raw={"nope": 1})])
        with pytest.raises(VerificationError):
            VeridianCrew(crew=bad_crew, sdk_context=ctx, task=task, contract=contract).kickoff({})

        # Round 2: repaired
        good_crew = _StubCrew(
            tasks=[_StubTaskOutput(description="draft", raw={"summary": "repaired"})]
        )
        VeridianCrew(crew=good_crew, sdk_context=ctx, task=task, contract=contract).kickoff({})

        # Replay report should show the journal and snapshot for audit.
        report = replay_run(ctx, task.id)
        assert report.task_id == task.id
        assert report.snapshot.get("model_id") == "mock/v1"


class TestCrewWithoutTaskCallback:
    def test_crew_without_task_callback_records_synthetic_final_step(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        """When the wrapped crew doesn't expose task_callback at all, the
        adapter falls back to recording a single 'final' step so the audit
        trail is never empty."""
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        class _MinimalCrew:
            def kickoff(self, inputs: dict[str, Any]) -> Any:
                return {"summary": "done"}

        wrapped = VeridianCrew(crew=_MinimalCrew(), sdk_context=ctx, task=task)
        result = wrapped.kickoff({})
        assert result == {"summary": "done"}
        assert any(s.metadata.get("node_id") == "final" for s in ctx.trace_steps)


class TestCrewErrorMapping:
    def test_runtime_error_is_wrapped_as_kickoff_error(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        class _BrokenCrew:
            task_callback: Callable[[Any], Any] | None = None

            def kickoff(self, inputs: dict[str, Any]) -> Any:
                raise RuntimeError("crew crashed")

        wrapped = VeridianCrew(crew=_BrokenCrew(), sdk_context=ctx, task=task)
        with pytest.raises(CrewKickoffError) as exc_info:
            wrapped.kickoff({})
        assert isinstance(exc_info.value, VeridianError)

    def test_attribute_error_is_wrapped_as_adapter_error(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)

        class _IncompatibleCrew:
            task_callback: Callable[[Any], Any] | None = None

            def kickoff(self, inputs: dict[str, Any]) -> Any:
                raise AttributeError("missing attribute")

        wrapped = VeridianCrew(crew=_IncompatibleCrew(), sdk_context=ctx, task=task)
        with pytest.raises(CrewAdapterError) as exc_info:
            wrapped.kickoff({})
        assert isinstance(exc_info.value, VeridianError)


class TestCrewSemantics:
    def test_hierarchical_crew_metadata_includes_manager_and_agent_roles(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        manager = _StubAgent(role="manager")
        analyst = _StubAgent(role="analyst")
        crew = _StubCrew(
            tasks=[_StubTaskOutput(description="analyze", raw={"summary": "ok"})],
        )
        crew.tasks = [_StubCrewTask(description="analyze", agent=analyst)]  # type: ignore[attr-defined]
        crew.process = "hierarchical"  # type: ignore[attr-defined]
        crew.manager_agent = manager  # type: ignore[attr-defined]

        wrapped = VeridianCrew(crew=crew, sdk_context=ctx, task=task)
        wrapped.kickoff({})

        step = next(s for s in ctx.trace_steps if s.metadata.get("node_id") == "analyze")
        assert step.metadata.get("manager_role") == "manager"
        assert step.metadata.get("agent_role") == "analyst"

    def test_unsupported_crewai_version_warns(
        self, sdk_env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        import warnings

        config, provider, ledger = sdk_env
        task = _seed_task(ledger)
        ctx = start_run(config=config, provider=provider, ledger=ledger)
        crew = _StubCrew(tasks=[])
        crew.__version__ = "0.50.0"  # type: ignore[attr-defined]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            VeridianCrew(crew=crew, sdk_context=ctx, task=task).kickoff({})
            compat = [item for item in caught if issubclass(item.category, CrewVersionWarning)]
            assert len(compat) == 1
