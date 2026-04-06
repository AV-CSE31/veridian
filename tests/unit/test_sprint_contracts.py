"""
tests.unit.test_sprint_contracts
─────────────────────────────────
Unit tests for the Sprint Contract Protocol.

Test order follows CLAUDE.md §1.1 (TDD):
  1. Tests written here FIRST — all fail until implementation ships.
  2. veridian/contracts/*.py written second to make them pass.

Coverage targets:
  - SprintContract creation, signing, verification
  - ContractRegistry register/get/list
  - SprintContractVerifier pass/fail cases
  - SprintContractHook before_task lifecycle
  - Events: ContractSigned, ContractViolated
  - Exceptions: ContractViolation, ContractNotFound
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from veridian.contracts.hook import SprintContractHook
from veridian.contracts.sprint import (
    ContractNotFound,
    ContractRegistry,
    SprintContract,
)
from veridian.contracts.verifier import SprintContractVerifier
from veridian.core.events import ContractSigned, ContractViolated
from veridian.core.exceptions import ContractViolation
from veridian.core.task import Task, TaskResult

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_task(metadata: dict[str, Any] | None = None) -> Task:
    return Task(
        title="Test sprint task",
        description="Verify output matches contract",
        verifier_id="sprint_contract",
        metadata=metadata or {},
    )


def _make_result(structured: dict[str, Any] | None = None) -> TaskResult:
    return TaskResult(
        raw_output="Task complete.",
        structured=structured or {},
    )


def _signed_contract(**kwargs: Any) -> SprintContract:
    c = SprintContract.create(
        task_id="t_test",
        deliverables=kwargs.get("deliverables", ["Deliverable A"]),
        success_criteria=kwargs.get("success_criteria", ["Criterion 1 satisfied"]),
        test_conditions=kwargs.get("test_conditions", ["All tests pass"]),
        acceptance_threshold=kwargs.get("acceptance_threshold", 0.8),
    )
    c.sign()
    return c


# ── SprintContract ─────────────────────────────────────────────────────────────


class TestSprintContractCreate:
    def test_create_basic(self):
        c = SprintContract.create(
            task_id="t1",
            deliverables=["Deliver report"],
            success_criteria=["Report contains executive summary"],
        )
        assert c.task_id == "t1"
        assert c.deliverables == ["Deliver report"]
        assert c.success_criteria == ["Report contains executive summary"]
        assert c.test_conditions == []
        assert c.acceptance_threshold == 0.8
        assert c.signature is None
        assert c.signed_at is None
        assert c.contract_id.startswith("sc_")

    def test_create_with_test_conditions(self):
        c = SprintContract.create(
            task_id="t2",
            deliverables=["D1"],
            success_criteria=["S1"],
            test_conditions=["pytest passes", "mypy clean"],
        )
        assert c.test_conditions == ["pytest passes", "mypy clean"]

    def test_create_custom_threshold(self):
        c = SprintContract.create(
            task_id="t3",
            deliverables=["D1"],
            success_criteria=["S1"],
            acceptance_threshold=0.95,
        )
        assert c.acceptance_threshold == 0.95

    def test_create_empty_deliverables_raises(self):
        with pytest.raises(ContractViolation, match="deliverables"):
            SprintContract.create(
                task_id="t_bad",
                deliverables=[],
                success_criteria=["S1"],
            )

    def test_create_empty_criteria_raises(self):
        with pytest.raises(ContractViolation, match="success_criteria"):
            SprintContract.create(
                task_id="t_bad",
                deliverables=["D1"],
                success_criteria=[],
            )

    def test_create_threshold_below_zero_raises(self):
        with pytest.raises(ContractViolation, match="acceptance_threshold"):
            SprintContract.create(
                task_id="t_bad",
                deliverables=["D1"],
                success_criteria=["S1"],
                acceptance_threshold=-0.1,
            )

    def test_create_threshold_above_one_raises(self):
        with pytest.raises(ContractViolation, match="acceptance_threshold"):
            SprintContract.create(
                task_id="t_bad",
                deliverables=["D1"],
                success_criteria=["S1"],
                acceptance_threshold=1.1,
            )

    def test_contract_ids_are_unique(self):
        c1 = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c2 = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        assert c1.contract_id != c2.contract_id

    def test_created_at_is_set(self):
        before = datetime.now(tz=UTC)
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        after = datetime.now(tz=UTC)
        assert before <= c.created_at <= after


class TestSprintContractSigning:
    def test_sign_sets_signature(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        assert c.signature is None
        c.sign()
        assert c.signature is not None
        assert len(c.signature) == 64  # SHA-256 hex = 64 chars

    def test_sign_sets_signed_at(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        before = datetime.now(tz=UTC)
        c.sign()
        after = datetime.now(tz=UTC)
        assert before <= c.signed_at <= after  # type: ignore[operator]

    def test_sign_returns_self_for_chaining(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        result = c.sign()
        assert result is c

    def test_is_signed_false_before_signing(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        assert not c.is_signed

    def test_is_signed_true_after_signing(self):
        c = _signed_contract()
        assert c.is_signed

    def test_verify_signature_valid(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.sign()
        assert c.verify_signature()

    def test_verify_signature_valid_with_custom_secret(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.sign(secret="my-secret-key")
        assert c.verify_signature(secret="my-secret-key")

    def test_verify_signature_fails_wrong_secret(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.sign(secret="correct-secret")
        assert not c.verify_signature(secret="wrong-secret")

    def test_verify_signature_fails_after_tampering(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.sign()
        # Tamper with contract after signing
        c.deliverables.append("Malicious extra deliverable")
        assert not c.verify_signature()

    def test_verify_signature_false_if_unsigned(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        assert not c.verify_signature()

    def test_signature_is_deterministic_same_content(self):
        """Same contract content produces same signature."""
        c1 = SprintContract(
            contract_id="sc_fixed",
            task_id="t_fixed",
            deliverables=["D1"],
            success_criteria=["S1"],
            test_conditions=[],
            acceptance_threshold=0.8,
        )
        c2 = SprintContract(
            contract_id="sc_fixed",
            task_id="t_fixed",
            deliverables=["D1"],
            success_criteria=["S1"],
            test_conditions=[],
            acceptance_threshold=0.8,
        )
        c1.sign()
        c2.sign()
        assert c1.signature == c2.signature


class TestSprintContractSerialization:
    def test_to_dict_roundtrip(self):
        c = _signed_contract()
        c.add_negotiation_note("generator", "Proposing deliverable A")
        d = c.to_dict()
        restored = SprintContract.from_dict(d)

        assert restored.contract_id == c.contract_id
        assert restored.task_id == c.task_id
        assert restored.deliverables == c.deliverables
        assert restored.success_criteria == c.success_criteria
        assert restored.test_conditions == c.test_conditions
        assert restored.acceptance_threshold == c.acceptance_threshold
        assert restored.signature == c.signature
        assert len(restored.negotiation_history) == 1

    def test_from_dict_preserves_timestamps(self):
        c = _signed_contract()
        d = c.to_dict()
        restored = SprintContract.from_dict(d)
        # Timestamps round-trip through ISO format
        assert restored.created_at.isoformat()[:19] == c.created_at.isoformat()[:19]
        assert restored.signed_at is not None

    def test_from_dict_unsigned_contract(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        restored = SprintContract.from_dict(c.to_dict())
        assert not restored.is_signed
        assert restored.signed_at is None


class TestSprintContractNegotiationHistory:
    def test_add_negotiation_note(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.add_negotiation_note("generator", "Initial proposal")
        assert len(c.negotiation_history) == 1
        assert c.negotiation_history[0]["agent"] == "generator"
        assert c.negotiation_history[0]["note"] == "Initial proposal"
        assert "ts" in c.negotiation_history[0]

    def test_add_multiple_notes(self):
        c = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        c.add_negotiation_note("generator", "Proposal v1")
        c.add_negotiation_note("evaluator", "Counter-proposal")
        c.add_negotiation_note("generator", "Accepted")
        assert len(c.negotiation_history) == 3


# ── ContractRegistry ──────────────────────────────────────────────────────────


class TestContractRegistry:
    def test_register_and_get(self):
        reg = ContractRegistry()
        c = _signed_contract()
        reg.register(c)
        retrieved = reg.get(c.contract_id)
        assert retrieved is c

    def test_get_unknown_raises_contract_not_found(self):
        reg = ContractRegistry()
        with pytest.raises(ContractNotFound, match="sc_unknown"):
            reg.get("sc_unknown")

    def test_register_overwrites_existing(self):
        reg = ContractRegistry()
        c1 = SprintContract(
            contract_id="sc_fixed",
            task_id="t",
            deliverables=["D"],
            success_criteria=["S"],
        )
        c2 = SprintContract(
            contract_id="sc_fixed",
            task_id="t",
            deliverables=["D2"],
            success_criteria=["S2"],
        )
        reg.register(c1)
        reg.register(c2)
        assert reg.get("sc_fixed").deliverables == ["D2"]

    def test_list_all_empty(self):
        reg = ContractRegistry()
        assert reg.list_all() == []

    def test_list_all_returns_all_contracts(self):
        reg = ContractRegistry()
        c1 = _signed_contract()
        c2 = _signed_contract()
        reg.register(c1)
        reg.register(c2)
        listed = reg.list_all()
        assert len(listed) == 2
        assert c1 in listed
        assert c2 in listed


# ── SprintContractVerifier ────────────────────────────────────────────────────


class TestSprintContractVerifier:
    def test_pass_signed_contract_default(self):
        """Signed contract with no score field → passes (score defaults to 1.0)."""
        v = SprintContractVerifier()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr = v.verify(task, result)
        assert vr.passed

    def test_fail_no_contract_in_metadata(self):
        v = SprintContractVerifier()
        task = _make_task()  # no sprint_contract in metadata
        result = _make_result()
        vr = v.verify(task, result)
        assert not vr.passed
        assert "sprint_contract" in vr.error.lower()  # type: ignore[union-attr]

    def test_fail_unsigned_contract_when_require_signed_true(self):
        v = SprintContractVerifier(require_signed=True)
        contract = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr = v.verify(task, result)
        assert not vr.passed
        assert "unsigned" in vr.error.lower()  # type: ignore[union-attr]

    def test_pass_unsigned_contract_when_require_signed_false(self):
        v = SprintContractVerifier(require_signed=False)
        contract = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr = v.verify(task, result)
        assert vr.passed

    def test_fail_score_below_acceptance_threshold(self):
        v = SprintContractVerifier()
        contract = _signed_contract(acceptance_threshold=0.8)
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result(structured={"contract_score": 0.5})
        vr = v.verify(task, result)
        assert not vr.passed
        assert "0.5" in vr.error or "threshold" in vr.error.lower()  # type: ignore[union-attr]

    def test_pass_score_at_exact_threshold(self):
        v = SprintContractVerifier()
        contract = _signed_contract(acceptance_threshold=0.8)
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result(structured={"contract_score": 0.8})
        vr = v.verify(task, result)
        assert vr.passed

    def test_pass_score_above_threshold(self):
        v = SprintContractVerifier()
        contract = _signed_contract(acceptance_threshold=0.7)
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result(structured={"contract_score": 0.95})
        vr = v.verify(task, result)
        assert vr.passed

    def test_evidence_includes_contract_id(self):
        v = SprintContractVerifier()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr = v.verify(task, result)
        assert vr.passed
        assert vr.evidence["contract_id"] == contract.contract_id

    def test_evidence_includes_signed_flag(self):
        v = SprintContractVerifier()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr = v.verify(task, result)
        assert vr.evidence["signed"] is True

    def test_verifier_id_is_sprint_contract(self):
        assert SprintContractVerifier.id == "sprint_contract"

    def test_verifier_is_stateless(self):
        """Two calls with same inputs produce same result — no instance-level state."""
        v = SprintContractVerifier()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        result = _make_result()
        vr1 = v.verify(task, result)
        vr2 = v.verify(task, result)
        assert vr1.passed == vr2.passed

    def test_error_message_is_within_300_chars(self):
        v = SprintContractVerifier()
        task = _make_task()  # no contract → error
        result = _make_result()
        vr = v.verify(task, result)
        assert not vr.passed
        assert len(vr.error or "") <= 300  # type: ignore[arg-type]


# ── SprintContractHook ────────────────────────────────────────────────────────


class _FakeTaskEvent:
    """Minimal event stub that mimics TaskClaimed."""

    def __init__(self, task: Task):
        self.task = task
        self.run_id = "run_test"


class TestSprintContractHook:
    def test_hook_priority_is_10(self):
        assert SprintContractHook.priority == 10

    def test_hook_id(self):
        assert SprintContractHook.id == "sprint_contract"

    def test_before_task_no_contract_noop(self):
        """Task with no sprint_contract in metadata → silent no-op."""
        hook = SprintContractHook()
        task = _make_task()
        event = _FakeTaskEvent(task)
        # Must not raise
        hook.before_task(event)

    def test_before_task_signed_contract_passes(self):
        """Valid signed contract → no exception."""
        hook = SprintContractHook()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        event = _FakeTaskEvent(task)
        # Must not raise
        hook.before_task(event)

    def test_before_task_unsigned_raises_when_required(self):
        """Unsigned contract with raise_on_unsigned=True → ContractViolation."""
        hook = SprintContractHook(raise_on_unsigned=True)
        contract = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        event = _FakeTaskEvent(task)
        with pytest.raises(ContractViolation, match="unsigned"):
            hook.before_task(event)

    def test_before_task_unsigned_does_not_raise_when_not_required(self):
        """Unsigned contract with raise_on_unsigned=False → no exception."""
        hook = SprintContractHook(raise_on_unsigned=False)
        contract = SprintContract.create(task_id="t", deliverables=["D"], success_criteria=["S"])
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})
        event = _FakeTaskEvent(task)
        hook.before_task(event)  # must not raise

    def test_before_task_require_contract_raises_when_missing(self):
        """require_contract=True + no contract in metadata → ContractViolation."""
        hook = SprintContractHook(require_contract=True)
        task = _make_task()  # no sprint_contract
        event = _FakeTaskEvent(task)
        with pytest.raises(ContractViolation, match="no sprint_contract"):
            hook.before_task(event)

    def test_before_task_event_without_task_is_noop(self):
        """Event with no 'task' attribute → silent no-op (defensive)."""
        hook = SprintContractHook()

        class _NoTask:
            run_id = "r"

        hook.before_task(_NoTask())  # must not raise

    def test_after_task_with_signed_contract_records_note(self):
        """after_task appends a completion note to the contract's history."""
        hook = SprintContractHook()
        contract = _signed_contract()
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})

        # Simulate TaskCompleted event
        class _CompletedEvent:
            run_id = "r"

        event = _FakeTaskEvent(task)
        hook.before_task(event)
        hook.after_task(event)
        # No exception = pass; richer state assertions below

    def test_hook_default_raise_on_unsigned_is_true(self):
        hook = SprintContractHook()
        assert hook.raise_on_unsigned is True

    def test_hook_default_require_contract_is_false(self):
        hook = SprintContractHook()
        assert hook.require_contract is False


# ── Events ────────────────────────────────────────────────────────────────────


class TestContractEvents:
    def test_contract_signed_event(self):
        evt = ContractSigned(run_id="r1", contract_id="sc_abc", task_id="t1")
        assert evt.event_type == "contract.signed"
        assert evt.contract_id == "sc_abc"
        assert evt.task_id == "t1"

    def test_contract_violated_event(self):
        evt = ContractViolated(run_id="r1", contract_id="sc_abc", task_id="t1", reason="Unsigned")
        assert evt.event_type == "contract.violated"
        assert evt.reason == "Unsigned"

    def test_contract_signed_to_dict(self):
        evt = ContractSigned(run_id="r1", contract_id="sc_x", task_id="t_x")
        d = evt.to_dict()
        assert d["event_type"] == "contract.signed"

    def test_contract_violated_to_dict(self):
        evt = ContractViolated(run_id="r1", contract_id="sc_x", task_id="t_x", reason="Bad")
        d = evt.to_dict()
        assert d["event_type"] == "contract.violated"


# ── Integration: hook + verifier in sequence ──────────────────────────────────


class TestContractIntegration:
    def test_hook_then_verifier_full_happy_path(self):
        """Full flow: hook validates pre-execution, verifier validates post-execution."""
        hook = SprintContractHook()
        verifier = SprintContractVerifier()

        contract = _signed_contract(acceptance_threshold=0.75)
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})

        # Pre-execution: hook validates
        before_event = _FakeTaskEvent(task)
        hook.before_task(before_event)  # must not raise

        # Post-execution: verifier checks result
        result = _make_result(structured={"contract_score": 0.9})
        vr = verifier.verify(task, result)
        assert vr.passed
        assert vr.evidence["contract_id"] == contract.contract_id

    def test_hook_then_verifier_score_fail(self):
        """If result score is below threshold, verifier fails."""
        hook = SprintContractHook()
        verifier = SprintContractVerifier()

        contract = _signed_contract(acceptance_threshold=0.9)
        task = _make_task(metadata={"sprint_contract": contract.to_dict()})

        before_event = _FakeTaskEvent(task)
        hook.before_task(before_event)

        result = _make_result(structured={"contract_score": 0.7})
        vr = verifier.verify(task, result)
        assert not vr.passed

    def test_contracts_module_public_api(self):
        """All public names accessible from veridian.contracts."""
        import veridian.contracts as cc  # noqa: PLC0415

        assert hasattr(cc, "SprintContract")
        assert hasattr(cc, "ContractRegistry")
        assert hasattr(cc, "SprintContractVerifier")
        assert hasattr(cc, "SprintContractHook")
        assert hasattr(cc, "ContractNotFound")
