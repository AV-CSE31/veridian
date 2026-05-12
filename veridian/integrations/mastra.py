"""
veridian.integrations.mastra
────────────────────────────
**Preview** adapter implementing the Mastra sidecar protocol.

Support level: ``preview``. Verification boundary: every Mastra workflow
step result is wrapped in a Veridian ``TaskResult`` and gated by the
registered verifier before the step's downstream outputs are considered
``DONE``.

Mastra is TypeScript-first; this Python sidecar exposes a small HTTP-free
protocol so a Mastra workflow can post step results to a long-lived
Veridian process over a stream of dicts. Use this for cross-language
agent workflows where the producer is Mastra and the verification
authority is Veridian.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from veridian.core.contract import VerificationContract
from veridian.core.exceptions import VeridianError
from veridian.core.task import Task
from veridian.integrations.sdk import RunContext, VerificationOutcome, verify_output

__all__ = [
    "MASTRA_SUPPORT_LEVEL",
    "MastraAdapterError",
    "MastraPreviewWarning",
    "MastraStep",
    "VeridianMastraSidecar",
]


MASTRA_SUPPORT_LEVEL: str = "preview"


class MastraAdapterError(VeridianError):
    """Mastra adapter error wrapped in the Veridian exception hierarchy."""


class MastraPreviewWarning(UserWarning):
    """Emitted on first construction to signal preview support level."""


@dataclass
class MastraStep:
    """A single Mastra workflow step delivered to the sidecar.

    Attributes:
        step_id: Mastra workflow step identifier.
        output: The step's produced output; passed to the verifier verbatim.
        contract: Optional per-step verification contract. When ``None`` the
            sidecar uses the task-level contract.
    """

    step_id: str
    output: Any
    contract: VerificationContract | None = None


@dataclass
class VeridianMastraSidecar:
    """Long-lived sidecar that verifies streamed Mastra workflow steps.

    Args:
        sdk_context: A :class:`RunContext` produced by ``start_run``.
        task: The task whose verification contract gates this workflow.
        contract: Optional task-level :class:`VerificationContract`.

    Usage::

        sidecar = VeridianMastraSidecar(sdk_context=ctx, task=task)
        for step in stream_from_mastra():
            outcome = sidecar.verify_step(step)
            send_to_mastra(outcome.to_dict() if hasattr(outcome, "to_dict") else vars(outcome))
    """

    sdk_context: RunContext
    task: Task
    contract: VerificationContract | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "VeridianMastraSidecar is a preview adapter; certified support is "
            "limited to LangGraph and CrewAI in v0.4.",
            MastraPreviewWarning,
            stacklevel=2,
        )

    def verify_step(self, step: MastraStep) -> VerificationOutcome:
        """Verify a single Mastra step and return the outcome."""
        contract = step.contract or self.contract
        verifier_id, verifier_config = self._resolve_verifier(contract)
        return verify_output(
            self.sdk_context,
            task=self.task,
            output=step.output,
            verifier_id=verifier_id,
            verifier_config=verifier_config,
        )

    def verify_stream(self, steps: Iterable[MastraStep]) -> Iterator[VerificationOutcome]:
        """Verify a stream of Mastra steps lazily."""
        for step in steps:
            yield self.verify_step(step)

    def _resolve_verifier(
        self, contract: VerificationContract | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        if contract is not None:
            return contract.verifier_id, dict(contract.verifier_config)
        return self.task.verifier_id, self.task.verifier_config
