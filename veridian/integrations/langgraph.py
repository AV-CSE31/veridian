"""
veridian.integrations.langgraph
-------------------------------
LangGraph adapter with verification, replay hooks, and compatibility guards.
"""

from __future__ import annotations

import importlib
import logging
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from veridian.core.exceptions import VeridianError
from veridian.core.task import Task, TraceStep
from veridian.integrations.sdk import (
    RunContext,
    persist_state,
    record_step,
    verify_output,
)

__all__ = [
    "LangGraphAdapterError",
    "LangGraphCompatibilityWarning",
    "VerificationContract",
    "VerificationError",
    "VeridianLangGraph",
]

logger = logging.getLogger(__name__)


@dataclass
class VerificationContract:
    """Per-node verification rules applied at edge transitions."""

    verifiers: dict[str, str] = field(default_factory=dict)
    verifier_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    on_failure: str = "raise"

    def has_verifier_for(self, node_id: str) -> bool:
        return node_id in self.verifiers


class LangGraphAdapterError(VeridianError):
    """Framework-level LangGraph error wrapped in the Veridian hierarchy."""


class LangGraphCompatibilityWarning(UserWarning):
    """Warning emitted when detected langgraph version is unsupported."""


class VerificationError(VeridianError):
    """Raised when a verified edge rejects output and on_failure='raise'."""

    def __init__(self, node_id: str, verifier_id: str, error: str) -> None:
        self.node_id = node_id
        self.verifier_id = verifier_id
        self.error = error
        super().__init__(f"Verification failed at node {node_id!r} ({verifier_id}): {error}")


class VeridianLangGraph:
    """Reliability wrapper around a LangGraph-style graph object."""

    SUPPORTED_VERSIONS: tuple[str, ...] = ("0.2", "0.3", "0.4")

    def __init__(
        self,
        graph: Any,
        sdk_context: RunContext,
        *,
        task: Task,
        contract: VerificationContract | None = None,
        on_node_complete: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.graph = graph
        self.ctx = sdk_context
        self.task = task
        self.contract = contract or VerificationContract()
        self.on_node_complete = on_node_complete

    def invoke(self, state: Any) -> Any:
        final_state: Any = state
        for _node_id, output in self.stream(state):
            final_state = output
        return final_state

    def stream(self, state: Any) -> Iterator[tuple[str, Any]]:
        """Yield (node_id, output) while mapping framework errors safely."""
        try:
            yield from self._stream_inner(state)
        except VeridianError:
            raise
        except Exception as exc:
            raise LangGraphAdapterError(
                f"LangGraph graph raised {type(exc).__name__}: {exc}"
            ) from exc

    def _stream_inner(self, state: Any) -> Iterator[tuple[str, Any]]:
        if hasattr(self.graph, "stream") and callable(self.graph.stream):
            for update in self.graph.stream(state):
                if isinstance(update, dict):
                    for node_id, node_output in update.items():
                        self._handle_node(node_id, node_output)
                        yield node_id, node_output
                else:
                    self._handle_node("graph", update)
                    yield "graph", update
            return

        if hasattr(self.graph, "invoke") and callable(self.graph.invoke):
            output = self.graph.invoke(state)
            self._handle_node("graph", output)
            yield "graph", output
            return

        raise LangGraphAdapterError(
            "VeridianLangGraph requires graph.stream or graph.invoke; got "
            f"{type(self.graph).__name__}"
        )

    def _detect_version(self) -> str | None:
        """Return installed langgraph version, or None when unavailable."""
        try:
            module = importlib.import_module("langgraph")
        except (ImportError, ModuleNotFoundError):
            return None
        version = getattr(module, "__version__", None)
        if isinstance(version, str):
            return version
        return None

    def _check_compatibility(self) -> None:
        """Warn if langgraph is missing or outside supported major.minor."""
        version = self._detect_version()
        if version is None:
            warnings.warn(
                "langgraph is not installed; adapter running in duck-typed mode",
                LangGraphCompatibilityWarning,
                stacklevel=2,
            )
            return
        major_minor = ".".join(version.split(".")[:2])
        if major_minor not in self.SUPPORTED_VERSIONS:
            warnings.warn(
                f"langgraph {version} is not in SUPPORTED_VERSIONS {self.SUPPORTED_VERSIONS}",
                LangGraphCompatibilityWarning,
                stacklevel=2,
            )
            return
        logger.debug("langgraph %s is within supported range", version)

    def _handle_node(self, node_id: str, output: Any) -> None:
        step = TraceStep(
            step_id=f"lg_{node_id}_{len(self.ctx.trace_steps) + 1}",
            role="assistant",
            action_type="reason",
            content=str(output)[:4000],
            timestamp_ms=int(time.time() * 1000),
            metadata={"node_id": node_id, "framework": "langgraph"},
        )
        record_step(self.ctx, step)

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
            if not outcome.passed and self.contract.on_failure == "raise":
                raise VerificationError(node_id, verifier_id, outcome.error or "unknown")

        if self.on_node_complete is not None:
            self.on_node_complete(node_id, output)

        if self.task.id:
            persist_state(self.ctx, task_id=self.task.id)
