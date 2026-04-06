"""
veridian.operator.runbooks
────────────────────────────
Runbook registry — structured incident-response procedures for common failures.

Ships with four built-in runbooks: stuck_task, cost_overrun, provider_failure,
and verification_loop. Operators can register additional runbooks at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

__all__ = [
    "Runbook",
    "RunbookRegistry",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runbook:
    """A structured incident-response procedure."""

    title: str
    symptoms: list[str] = field(default_factory=list)
    diagnosis_steps: list[str] = field(default_factory=list)
    resolution_steps: list[str] = field(default_factory=list)
    escalation: str = ""


# ── Built-in runbooks ────────────────────────────────────────────────────────


def _builtin_stuck_task() -> Runbook:
    return Runbook(
        title="stuck-task",
        symptoms=[
            "Task stuck in IN_PROGRESS for longer than expected",
            "No progress updates from agent",
            "Task duration exceeds timeout threshold",
        ],
        diagnosis_steps=[
            "Check task status in ledger: veridian status <task_id>",
            "Review agent logs for the task",
            "Check if the provider is responding",
            "Verify the task is not waiting on an external resource",
        ],
        resolution_steps=[
            "If agent is hung: kill the runner process and restart",
            "If provider is down: wait for recovery or switch provider",
            "If task is malformed: move to DLQ and fix task spec",
            "Force-fail the task: veridian fail <task_id> --reason 'manual intervention'",
        ],
        escalation="If task remains stuck after manual intervention, escalate to platform team.",
    )


def _builtin_cost_overrun() -> Runbook:
    return Runbook(
        title="cost-overrun",
        symptoms=[
            "CostLimitExceeded error raised",
            "Budget exceeded warning in logs",
            "Unexpectedly high token usage",
            "cost budget alert",
        ],
        diagnosis_steps=[
            "Check current spend: veridian budget status",
            "Review cost breakdown by task",
            "Identify which tasks are consuming the most tokens",
            "Check for retry loops amplifying cost",
        ],
        resolution_steps=[
            "Increase budget limit if spend is justified",
            "Kill expensive tasks that are not making progress",
            "Switch to a cheaper model for low-priority tasks",
            "Add cost guards to prevent runaway spending",
        ],
        escalation="If cost exceeds 2x budget, halt all runs and escalate to finance.",
    )


def _builtin_provider_failure() -> Runbook:
    return Runbook(
        title="provider-failure",
        symptoms=[
            "ProviderError or ProviderRateLimited raised",
            "Multiple tasks failing with connection errors",
            "Circuit breaker opened for provider",
            "provider timeout cascade",
        ],
        diagnosis_steps=[
            "Check provider status page",
            "Review error rates in observability dashboard",
            "Verify API key is valid and not rotated",
            "Check rate limit headers from recent responses",
        ],
        resolution_steps=[
            "If rate limited: back off and retry with exponential delay",
            "If provider is down: switch to fallback provider",
            "If API key expired: rotate credentials",
            "If circuit breaker opened: wait for half-open window or reset manually",
        ],
        escalation="If provider outage exceeds 30 minutes, switch to backup provider and notify team.",
    )


def _builtin_verification_loop() -> Runbook:
    return Runbook(
        title="verification-loop",
        symptoms=[
            "Task oscillating between IN_PROGRESS and FAILED",
            "Retry count increasing without progress",
            "Same verification error repeating",
            "verification loop detected",
        ],
        diagnosis_steps=[
            "Check task retry count and history",
            "Review verification errors for the task",
            "Compare agent output across retries for changes",
            "Check if verifier config matches task requirements",
        ],
        resolution_steps=[
            "If verifier config is wrong: fix and re-submit",
            "If agent is unable to satisfy verifier: escalate to task author",
            "If flaky verifier: add tolerance or switch to deterministic verifier",
            "Move to DLQ if retries exhausted: veridian dlq enqueue <task_id>",
        ],
        escalation="If loop persists after config fix, escalate to verifier maintainer.",
    )


class RunbookRegistry:
    """Registry of incident-response runbooks.

    Use :meth:`with_builtins` to get a registry pre-loaded with the four
    built-in runbooks.
    """

    def __init__(self) -> None:
        self._runbooks: dict[str, Runbook] = {}

    @classmethod
    def with_builtins(cls) -> RunbookRegistry:
        """Create a registry with the four built-in runbooks pre-registered."""
        reg = cls()
        reg.register(_builtin_stuck_task())
        reg.register(_builtin_cost_overrun())
        reg.register(_builtin_provider_failure())
        reg.register(_builtin_verification_loop())
        return reg

    def register(self, runbook: Runbook) -> None:
        """Register a runbook. Overwrites if title already exists."""
        self._runbooks[runbook.title] = runbook
        log.info("runbook.registered title=%s", runbook.title)

    def lookup_by_symptom(self, symptom_text: str) -> list[Runbook]:
        """Find runbooks whose symptoms match the given text (case-insensitive).

        Returns all runbooks where at least one symptom contains the search
        text as a substring.
        """
        needle = symptom_text.lower()
        matches: list[Runbook] = []
        for rb in self._runbooks.values():
            for symptom in rb.symptoms:
                if needle in symptom.lower():
                    matches.append(rb)
                    break
        return matches

    def list_all(self) -> list[Runbook]:
        """Return all registered runbooks."""
        return list(self._runbooks.values())
