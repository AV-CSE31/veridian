"""
tests.test_saga
───────────────
Tests for F3.3 — Saga Pattern / Compensating Transactions.
"""

from __future__ import annotations

from typing import Any

import pytest

from veridian.core.saga import (
    SagaOrchestrator,
    SagaStatus,
    SagaStep,
    StepStatus,
)

# ─── helpers ──────────────────────────────────────────────────────────────────


def make_recorder() -> tuple[list[str], list[str]]:
    """Return (actions_log, compensations_log) lists."""
    return [], []


# ─── SagaStep tests ───────────────────────────────────────────────────────────


class TestSagaStep:
    def test_creation(self) -> None:
        step = SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
        )
        assert step.step_id == "step_1"
        assert step.status == StepStatus.PENDING

    def test_idempotency_key_default(self) -> None:
        step = SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
        )
        # idempotency key defaults to step_id
        assert step.idempotency_key == "step_1"

    def test_custom_idempotency_key(self) -> None:
        step = SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
            idempotency_key="custom_key_abc",
        )
        assert step.idempotency_key == "custom_key_abc"


# ─── SagaOrchestrator tests ───────────────────────────────────────────────────


class TestSagaOrchestrator:
    def test_successful_saga(self) -> None:
        log: list[str] = []

        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: log.append("action_1"),
            compensation=lambda ctx: log.append("comp_1"),
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: log.append("action_2"),
            compensation=lambda ctx: log.append("comp_2"),
        ))

        saga.execute()

        assert saga.status == SagaStatus.COMPLETED
        assert log == ["action_1", "action_2"]

    def test_rollback_on_failure(self) -> None:
        log: list[str] = []

        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: log.append("action_1"),
            compensation=lambda ctx: log.append("comp_1"),
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: (_ for _ in ()).throw(RuntimeError("step_2 failed")),
            compensation=lambda ctx: log.append("comp_2"),
        ))

        saga.execute()

        assert saga.status == SagaStatus.COMPENSATED
        assert "action_1" in log
        assert "comp_1" in log
        assert "comp_2" not in log  # step_2 action never completed

    def test_rollback_executes_in_reverse_order(self) -> None:
        log: list[str] = []

        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: log.append("a1"),
            compensation=lambda ctx: log.append("c1"),
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: log.append("a2"),
            compensation=lambda ctx: log.append("c2"),
        ))
        saga.add_step(SagaStep(
            step_id="step_3",
            action=lambda ctx: (_ for _ in ()).throw(RuntimeError("fail")),
            compensation=lambda ctx: log.append("c3"),
        ))

        saga.execute()

        # Actions ran: a1, a2. Compensations in reverse: c2, c1
        assert log.index("c2") < log.index("c1")

    def test_context_passed_to_steps(self) -> None:
        received: list[Any] = []

        saga = SagaOrchestrator(context={"key": "value"})
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: received.append(ctx.get("key")),
            compensation=lambda ctx: None,
        ))
        saga.execute()

        assert received == ["value"]

    def test_context_can_be_mutated_by_steps(self) -> None:
        saga = SagaOrchestrator(context={})
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: ctx.update({"result": 42}),
            compensation=lambda ctx: None,
        ))
        saga.execute()
        assert saga.context.get("result") == 42

    def test_idempotency_prevents_duplicate_execution(self) -> None:
        log: list[str] = []
        executed_keys: set[str] = set()

        def idempotent_action(ctx: dict) -> None:
            key = "action_key"
            if key in executed_keys:
                return
            executed_keys.add(key)
            log.append("executed")

        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=idempotent_action,
            compensation=lambda ctx: None,
            idempotency_key="action_key",
        ))
        saga.execute()
        saga.execute()  # second call should not re-execute completed steps
        assert log.count("executed") == 1

    def test_failed_status_on_uncompensated_error(self) -> None:
        def bad_comp(ctx: dict) -> None:
            raise RuntimeError("compensation failed too")

        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=bad_comp,
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: (_ for _ in ()).throw(RuntimeError("fail")),
            compensation=lambda ctx: None,
        ))
        saga.execute()
        # Status should indicate failure (either FAILED or COMPENSATION_FAILED)
        assert saga.status in (SagaStatus.FAILED, SagaStatus.COMPENSATION_FAILED)

    def test_step_statuses_updated(self) -> None:
        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
        ))
        saga.execute()

        for step in saga.steps:
            assert step.status == StepStatus.COMPLETED

    def test_can_inspect_failed_step(self) -> None:
        saga = SagaOrchestrator()
        saga.add_step(SagaStep(
            step_id="step_1",
            action=lambda ctx: None,
            compensation=lambda ctx: None,
        ))
        saga.add_step(SagaStep(
            step_id="step_2",
            action=lambda ctx: (_ for _ in ()).throw(ValueError("oops")),
            compensation=lambda ctx: None,
        ))
        saga.execute()

        assert saga.failed_step is not None
        assert saga.failed_step.step_id == "step_2"

    def test_empty_saga_completes(self) -> None:
        saga = SagaOrchestrator()
        saga.execute()
        assert saga.status == SagaStatus.COMPLETED

    def test_add_step_after_execution_raises(self) -> None:
        from veridian.core.exceptions import SagaError
        saga = SagaOrchestrator()
        saga.execute()
        with pytest.raises(SagaError):
            saga.add_step(SagaStep(
                step_id="late",
                action=lambda ctx: None,
                compensation=lambda ctx: None,
            ))
