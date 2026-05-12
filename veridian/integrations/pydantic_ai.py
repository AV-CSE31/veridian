"""
veridian.integrations.pydantic_ai
─────────────────────────────────
**Preview** adapter for Pydantic AI's durable-execution sidecar pattern.

Support level: ``preview``. Tested versions: pydantic-ai >= 0.0.13.
Verification boundary: every Pydantic AI ``Agent.run`` result is wrapped in a
Veridian ``TaskResult`` and run through the registered verifier; the agent
is the producer, the Veridian verifier is the independent authority that
admits the result to ``DONE``.

This file ships as a thin sidecar so consumers don't have to wire the SDK
helpers by hand. The certified adapter set remains LangGraph and CrewAI
until Pydantic AI has its own certification suite (see
``guides/production/roadmap-v0.5.md``).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from veridian.core.contract import VerificationContract
from veridian.core.exceptions import VeridianError
from veridian.core.task import Task
from veridian.integrations.sdk import RunContext, VerificationOutcome, verify_output

__all__ = [
    "PYDANTIC_AI_SUPPORT_LEVEL",
    "PydanticAIAdapterError",
    "PydanticAIPreviewWarning",
    "VeridianPydanticAI",
]


PYDANTIC_AI_SUPPORT_LEVEL: str = "preview"


class PydanticAIAdapterError(VeridianError):
    """Pydantic AI adapter error wrapped in the Veridian exception hierarchy."""


class PydanticAIPreviewWarning(UserWarning):
    """Emitted on first construction to signal preview support level."""


@dataclass
class VeridianPydanticAI:
    """Wrap a Pydantic AI ``Agent`` so every run goes through a Veridian verifier.

    Args:
        agent: A Pydantic AI ``Agent`` instance (or any object exposing a
            ``run_sync(prompt) -> RunResult``-like method).
        sdk_context: A :class:`RunContext` produced by ``start_run``.
        task: The task whose verification contract gates this run.
        contract: Optional explicit :class:`VerificationContract`. When ``None``
            the contract is derived from ``task.verifier_id`` /
            ``task.verifier_config``.

    Usage::

        from veridian.integrations.sdk import start_run
        from veridian.integrations.pydantic_ai import VeridianPydanticAI

        ctx = start_run(config=config, provider=provider)
        wrapper = VeridianPydanticAI(agent=my_agent, sdk_context=ctx, task=task)
        outcome = wrapper.run_sync("Summarize ticket 1234")
        assert outcome.passed  # falsy if the verifier rejected the output
    """

    agent: Any
    sdk_context: RunContext
    task: Task
    contract: VerificationContract | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "VeridianPydanticAI is a preview adapter; certified support is "
            "limited to LangGraph and CrewAI in v0.4.",
            PydanticAIPreviewWarning,
            stacklevel=2,
        )

    def run_sync(self, prompt: str, **kwargs: Any) -> VerificationOutcome:
        """Run the Pydantic AI agent synchronously and verify the result.

        Raises:
            PydanticAIAdapterError: if the underlying agent raises.
        """
        run_sync = getattr(self.agent, "run_sync", None)
        if run_sync is None or not callable(run_sync):
            raise PydanticAIAdapterError(
                f"agent {type(self.agent).__name__} has no callable run_sync"
            )

        try:
            agent_result = run_sync(prompt, **kwargs)
        except Exception as exc:
            raise PydanticAIAdapterError(
                f"Pydantic AI agent raised {type(exc).__name__}: {exc}"
            ) from exc

        # Pydantic AI returns ``RunResult`` with ``.data``; fall back to repr
        # for other shapes.
        output = getattr(agent_result, "data", agent_result)

        verifier_id, verifier_config = self._resolve_verifier()
        return verify_output(
            self.sdk_context,
            task=self.task,
            output=output,
            verifier_id=verifier_id,
            verifier_config=verifier_config,
        )

    def _resolve_verifier(self) -> tuple[str | None, dict[str, Any] | None]:
        if self.contract is not None:
            return self.contract.verifier_id, dict(self.contract.verifier_config)
        return self.task.verifier_id, self.task.verifier_config
