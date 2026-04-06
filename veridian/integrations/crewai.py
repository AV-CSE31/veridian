"""
veridian.integrations.crewai
----------------------------
CrewAI adapter with reliability hooks, semantic mapping, and compat checks.
"""

from __future__ import annotations

import contextlib
import importlib
import re
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from veridian.core.exceptions import VeridianError
from veridian.core.task import Task, TraceStep
from veridian.integrations.langgraph import VerificationContract, VerificationError
from veridian.integrations.sdk import (
    RunContext,
    persist_state,
    record_step,
    verify_output,
)

__all__ = [
    "CrewAdapterError",
    "CrewDelegationError",
    "CrewKickoffError",
    "CrewVerificationContract",
    "CrewVersionWarning",
    "SUPPORTED_VERSIONS",
    "VeridianCrew",
]


class CrewAdapterError(VeridianError):
    """CrewAI adapter compatibility or framework interaction error."""


class CrewKickoffError(CrewAdapterError):
    """Crew kickoff failed after wrapping a framework-level exception."""

    def __init__(self, detail: str, cause: Exception | None = None) -> None:
        self.detail = detail
        self.cause = cause
        super().__init__(f"CrewAI kickoff failed: {detail}")


class CrewDelegationError(CrewAdapterError):
    """Crew task delegation failed."""

    def __init__(self, task_id: str, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"CrewAI delegation failed for task {task_id!r}: {reason}")


class CrewVersionWarning(UserWarning):
    """Warning emitted when detected CrewAI version is unsupported."""


SUPPORTED_VERSIONS: tuple[tuple[int, int], ...] = (
    (0, 80),
    (0, 81),
    (0, 82),
    (0, 83),
    (0, 84),
    (0, 85),
    (0, 86),
)
_MIN_SUPPORTED = SUPPORTED_VERSIONS[0]


# Reuse the LangGraph contract shape - same primitives, different wrapper.
CrewVerificationContract = VerificationContract


@dataclass
class _StepRecord:
    """Internal audit entry for one CrewAI task output."""

    task_id: str
    output: Any
    verified: bool
    verifier_id: str | None
    error: str | None


def _detect_version(crew: Any) -> str | None:
    version = getattr(crew, "__version__", None)
    if isinstance(version, str):
        return version
    try:
        module = importlib.import_module("crewai")
    except (ImportError, ModuleNotFoundError):
        return None
    module_version = getattr(module, "__version__", None)
    if isinstance(module_version, str):
        return module_version
    return None


def _parse_version_tuple(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _check_compatibility(crew: Any) -> None:
    version = _detect_version(crew)
    if version is None:
        return
    parsed = _parse_version_tuple(version)
    if parsed is None:
        return
    if parsed < _MIN_SUPPORTED:
        warnings.warn(
            f"CrewAI version {version} is below minimum supported "
            f"{_MIN_SUPPORTED[0]}.{_MIN_SUPPORTED[1]}.x",
            CrewVersionWarning,
            stacklevel=3,
        )


def _build_agent_map(crew: Any) -> dict[str, str]:
    """Map task description -> agent role when crew metadata is available."""
    mapping: dict[str, str] = {}
    crew_tasks = getattr(crew, "tasks", None)
    if not isinstance(crew_tasks, list):
        return mapping
    for crew_task in crew_tasks:
        description = getattr(crew_task, "description", None)
        agent = getattr(crew_task, "agent", None)
        role = getattr(agent, "role", None) if agent is not None else None
        if description is not None and role is not None:
            mapping[str(description)] = str(role)
    return mapping


class VeridianCrew:
    """Reliability wrapper around a CrewAI Crew or Flow-like object."""

    def __init__(
        self,
        crew: Any,
        sdk_context: RunContext,
        *,
        task: Task,
        contract: CrewVerificationContract | None = None,
    ) -> None:
        self.crew = crew
        self.ctx = sdk_context
        self.task = task
        self.contract = contract or CrewVerificationContract()
        self._step_records: list[_StepRecord] = []
        self._agent_map = _build_agent_map(crew)
        self._manager_role = self._detect_manager_role()
        _check_compatibility(crew)

    def _detect_manager_role(self) -> str | None:
        process = getattr(self.crew, "process", None)
        if process != "hierarchical":
            return None
        manager = getattr(self.crew, "manager_agent", None)
        if manager is None:
            return None
        role = getattr(manager, "role", None)
        return str(role) if role is not None else "manager"

    def kickoff(self, inputs: dict[str, Any] | None = None) -> Any:
        """Run crew kickoff with verification+checkpoint interception."""
        original_cb = getattr(self.crew, "task_callback", None)
        wrapped_cb = self._build_task_callback(original_cb)
        if hasattr(self.crew, "task_callback"):
            with contextlib.suppress(Exception):
                self.crew.task_callback = wrapped_cb

        try:
            result = self.crew.kickoff(inputs or {})
        except VerificationError:
            raise
        except (RuntimeError, OSError, ConnectionError) as exc:
            raise CrewKickoffError(str(exc), cause=exc) from exc
        except (AttributeError, TypeError) as exc:
            raise CrewAdapterError(f"CrewAI adapter compatibility error: {exc}") from exc

        if not self._step_records:
            self._record("final", result)

        return result

    def verify_task_output(self, crew_task_id: str, output: Any) -> None:
        self._record(crew_task_id, output)

    def _build_task_callback(self, original: Callable[..., Any] | None) -> Callable[..., Any]:
        def _callback(task_output: Any) -> Any:
            node_id = getattr(task_output, "description", None) or getattr(
                task_output, "name", "task"
            )
            raw_output = getattr(task_output, "raw", None) or task_output
            self._record(str(node_id), raw_output)
            if original is not None:
                return original(task_output)
            return task_output

        return _callback

    def _record(self, node_id: str, output: Any) -> None:
        metadata: dict[str, Any] = {"node_id": node_id, "framework": "crewai"}
        agent_role = self._agent_map.get(node_id)
        if agent_role is not None:
            metadata["agent_role"] = agent_role
        if self._manager_role is not None:
            metadata["manager_role"] = self._manager_role
            metadata["process"] = "hierarchical"

        step = TraceStep(
            step_id=f"crewai_{node_id}_{len(self.ctx.trace_steps) + 1}",
            role="assistant",
            action_type="reason",
            content=str(output)[:4000],
            timestamp_ms=int(time.time() * 1000),
            metadata=metadata,
        )
        record_step(self.ctx, step)

        verifier_id: str | None = None
        error_text: str | None = None
        verified = True
        if self.contract.has_verifier_for(node_id):
            verifier_id = self.contract.verifiers[node_id]
            verifier_cfg = self.contract.verifier_configs.get(node_id, {})
            outcome = verify_output(
                self.ctx,
                task=self.task,
                output=output,
                verifier_id=verifier_id,
                verifier_config=verifier_cfg,
            )
            verified = outcome.passed
            error_text = outcome.error
            if not outcome.passed and self.contract.on_failure == "raise":
                raise VerificationError(node_id, verifier_id, error_text or "unknown")

        self._step_records.append(
            _StepRecord(
                task_id=node_id,
                output=output,
                verified=verified,
                verifier_id=verifier_id,
                error=error_text,
            )
        )
        if self.task.id:
            persist_state(self.ctx, task_id=self.task.id)

    @property
    def step_records(self) -> list[_StepRecord]:
        return list(self._step_records)
