"""
veridian.integrations.openai_agents
───────────────────────────────────
**Preview** adapter for the OpenAI Agents SDK guardrail bridge.

Support level: ``preview``. Verification boundary: every OpenAI Agents SDK
``Runner.run`` result is passed through a Veridian verifier *before* the
SDK is allowed to consider the agent step complete. The Veridian outcome
is exposed as an OpenAI-compatible guardrail decision (``allow`` /
``block``) for downstream tool-use orchestration.
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
    "OPENAI_AGENTS_SUPPORT_LEVEL",
    "GuardrailDecision",
    "OpenAIAgentsAdapterError",
    "OpenAIAgentsPreviewWarning",
    "VeridianOpenAIAgentsGuardrail",
]


OPENAI_AGENTS_SUPPORT_LEVEL: str = "preview"


class OpenAIAgentsAdapterError(VeridianError):
    """OpenAI Agents SDK adapter error wrapped in the Veridian hierarchy."""


class OpenAIAgentsPreviewWarning(UserWarning):
    """Emitted on first construction to signal preview support level."""


@dataclass
class GuardrailDecision:
    """OpenAI-compatible guardrail decision derived from a Veridian outcome.

    Attributes:
        allow: ``True`` iff the verifier passed; SDK should accept the step.
        reason: Verifier error string on block; empty on allow.
        outcome: The full :class:`VerificationOutcome` for downstream audit.
    """

    allow: bool
    reason: str
    outcome: VerificationOutcome

    @classmethod
    def from_outcome(cls, outcome: VerificationOutcome) -> GuardrailDecision:
        return cls(
            allow=outcome.passed,
            reason="" if outcome.passed else (outcome.error or "verifier rejected output"),
            outcome=outcome,
        )


@dataclass
class VeridianOpenAIAgentsGuardrail:
    """Convert a Veridian verifier into an OpenAI Agents SDK guardrail.

    Args:
        sdk_context: A :class:`RunContext` produced by ``start_run``.
        task: The task whose verification contract gates this guardrail.
        contract: Optional explicit :class:`VerificationContract`.

    Usage::

        guardrail = VeridianOpenAIAgentsGuardrail(
            sdk_context=ctx, task=task,
        )
        result = openai_runner.run(...)
        decision = guardrail.check(result.final_output)
        if not decision.allow:
            raise RuntimeError(decision.reason)
    """

    sdk_context: RunContext
    task: Task
    contract: VerificationContract | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "VeridianOpenAIAgentsGuardrail is a preview adapter; certified "
            "support is limited to LangGraph and CrewAI in v0.4.",
            OpenAIAgentsPreviewWarning,
            stacklevel=2,
        )

    def check(self, output: Any) -> GuardrailDecision:
        """Run the verifier and return a guardrail decision."""
        verifier_id, verifier_config = self._resolve_verifier()
        outcome = verify_output(
            self.sdk_context,
            task=self.task,
            output=output,
            verifier_id=verifier_id,
            verifier_config=verifier_config,
        )
        return GuardrailDecision.from_outcome(outcome)

    def _resolve_verifier(self) -> tuple[str | None, dict[str, Any] | None]:
        if self.contract is not None:
            return self.contract.verifier_id, dict(self.contract.verifier_config)
        return self.task.verifier_id, self.task.verifier_config
