"""
veridian.core.saga
──────────────────
Saga Pattern / Compensating Transactions for multi-step agent tasks.

Each step registers:
  - action:       the forward operation
  - compensation: the undo operation (run if a later step fails)

On failure at step N, the orchestrator runs compensations for steps
N-1 → 1 in reverse order.

Idempotency keys prevent duplicate execution if execute() is called
more than once on a completed saga.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from veridian.core.exceptions import SagaError, SagaRollbackError

log = logging.getLogger(__name__)


class SagaStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATED = "compensated"
    FAILED = "failed"
    COMPENSATION_FAILED = "compensation_failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"


@dataclass
class SagaStep:
    """
    A single saga step with its forward action and compensation.

    Args:
        step_id:          Unique identifier within the saga.
        action:           Callable(ctx: dict) → None. The forward operation.
        compensation:     Callable(ctx: dict) → None. The undo operation.
        idempotency_key:  Defaults to step_id. Steps with the same key
                          are skipped if already COMPLETED.
    """

    step_id: str
    action: Callable[[dict[str, Any]], None]
    compensation: Callable[[dict[str, Any]], None]
    idempotency_key: str = ""
    status: StepStatus = StepStatus.PENDING
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = self.step_id


class SagaOrchestrator:
    """
    Manages saga execution and rollback.

    Usage::

        saga = SagaOrchestrator(context={"order_id": "abc"})
        saga.add_step(SagaStep(
            step_id="reserve_inventory",
            action=lambda ctx: reserve(ctx["order_id"]),
            compensation=lambda ctx: release(ctx["order_id"]),
        ))
        saga.add_step(SagaStep(
            step_id="charge_payment",
            action=lambda ctx: charge(ctx["order_id"]),
            compensation=lambda ctx: refund(ctx["order_id"]),
        ))
        saga.execute()

        if saga.status == SagaStatus.COMPLETED:
            ...
        elif saga.status == SagaStatus.COMPENSATED:
            # rollback succeeded
            ...
    """

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context: dict[str, Any] = context or {}
        self.steps: list[SagaStep] = []
        self.status: SagaStatus = SagaStatus.PENDING
        self.failed_step: SagaStep | None = None
        self._executed_idempotency_keys: set[str] = set()

    # ── Configuration ──────────────────────────────────────────────────────────

    def add_step(self, step: SagaStep) -> None:
        """
        Append a step. Raises SagaError if the saga has already been executed.
        """
        if self.status not in (SagaStatus.PENDING, SagaStatus.RUNNING):
            raise SagaError(
                f"Cannot add steps to a saga with status '{self.status}'. "
                f"Create a new SagaOrchestrator for a new saga."
            )
        self.steps.append(step)

    # ── Execution ──────────────────────────────────────────────────────────────

    def execute(self) -> None:
        """
        Execute all steps in order. On failure, compensate completed steps
        in reverse order.

        Idempotency: steps whose idempotency_key is already in the completed
        set are silently skipped.
        """
        if self.status == SagaStatus.COMPLETED:
            log.debug("saga.execute: already completed, skipping")
            return

        self.status = SagaStatus.RUNNING
        completed_steps: list[SagaStep] = []

        for step in self.steps:
            # Idempotency check
            if step.idempotency_key in self._executed_idempotency_keys:
                log.debug("saga.step.skip idempotency_key=%s", step.idempotency_key)
                step.status = StepStatus.COMPLETED
                completed_steps.append(step)
                continue

            step.status = StepStatus.RUNNING
            try:
                step.action(self.context)
                step.status = StepStatus.COMPLETED
                self._executed_idempotency_keys.add(step.idempotency_key)
                completed_steps.append(step)
                log.debug("saga.step.completed step_id=%s", step.step_id)
            except Exception as exc:
                step.status = StepStatus.FAILED
                step.error = str(exc)
                self.failed_step = step
                log.warning(
                    "saga.step.failed step_id=%s error=%s",
                    step.step_id,
                    exc,
                )
                # Compensate all previously completed steps in reverse
                self._compensate(completed_steps)
                return

        self.status = SagaStatus.COMPLETED
        log.info("saga.completed steps=%d", len(self.steps))

    # ── Compensation ───────────────────────────────────────────────────────────

    def _compensate(self, completed_steps: list[SagaStep]) -> None:
        """Run compensations in reverse order for all completed steps."""
        failed_compensations: list[str] = []

        for step in reversed(completed_steps):
            step.status = StepStatus.COMPENSATING
            try:
                step.compensation(self.context)
                step.status = StepStatus.COMPENSATED
                log.debug("saga.compensation.done step_id=%s", step.step_id)
            except Exception as exc:
                step.status = StepStatus.FAILED
                step.error = str(exc)
                failed_compensations.append(step.step_id)
                log.error(
                    "saga.compensation.failed step_id=%s error=%s",
                    step.step_id,
                    exc,
                )

        if failed_compensations:
            self.status = SagaStatus.COMPENSATION_FAILED
            log.error(
                "saga.compensation.partial_failure steps=%s",
                failed_compensations,
            )
        else:
            self.status = SagaStatus.COMPENSATED


__all__ = ["SagaStep", "SagaOrchestrator", "SagaStatus", "StepStatus"]
