"""
tests/unit/test_handoff.py
───────────────────────────
Unit tests for the Multi-Agent Handoff Protocol (F2.2).

Covers:
  - HandoffPacket: creation, HMAC token, serialization
  - HandoffProtocol: create_packet, verify_packet, conditional handoff
  - Verification gate: no unverified state crosses agent boundaries
  - Audit log: handoffs are recorded
  - Exception hierarchy: HandoffVerificationFailed, HandoffIntegrityError
"""

from __future__ import annotations

import pytest

from veridian.agents.handoff import HandoffPacket, HandoffProtocol
from veridian.core.exceptions import HandoffIntegrityError, HandoffVerificationFailed
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


class AlwaysPassVerifier(BaseVerifier):
    id = "always_pass"
    description = "Passes every time"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True, evidence={"reason": "always"})


class AlwaysFailVerifier(BaseVerifier):
    id = "always_fail"
    description = "Fails every time"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="Forced failure for testing")


@pytest.fixture
def task() -> Task:
    return Task(id="task-001", title="Process invoice", description="Extract fields from invoice.")


@pytest.fixture
def result() -> TaskResult:
    return TaskResult(
        raw_output="Invoice fields extracted.",
        structured={"amount": 100.0, "vendor": "Acme"},
        verified=True,
    )


@pytest.fixture
def secret_key() -> bytes:
    return b"test-secret-key-32-bytes-min!!!!"


@pytest.fixture
def passing_protocol(secret_key: bytes) -> HandoffProtocol:
    return HandoffProtocol(
        verifiers=[AlwaysPassVerifier()],
        secret_key=secret_key,
    )


@pytest.fixture
def failing_protocol(secret_key: bytes) -> HandoffProtocol:
    return HandoffProtocol(
        verifiers=[AlwaysFailVerifier()],
        secret_key=secret_key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# HandoffPacket tests
# ─────────────────────────────────────────────────────────────────────────────


class TestHandoffPacket:
    def test_packet_has_unique_id(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        p1 = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        p2 = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert p1.packet_id != p2.packet_id

    def test_packet_contains_task_state(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert packet.task_id == task.id
        assert "title" in packet.task_state

    def test_packet_contains_verification_history(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert isinstance(packet.verification_history, list)
        assert len(packet.verification_history) >= 1  # verification ran

    def test_packet_has_hmac_token(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert packet.hmac_token
        assert len(packet.hmac_token) == 64  # SHA-256 hex

    def test_packet_has_context_summary(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(
            task,
            result,
            source_agent_id="agent-A",
            context_summary="Processed invoice data for downstream analysis.",
        )
        assert packet.context_summary == "Processed invoice data for downstream analysis."

    def test_packet_has_constraints(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(
            task,
            result,
            source_agent_id="agent-A",
            constraints=["max_cost_usd=1.0", "require_human_review=false"],
        )
        assert "max_cost_usd=1.0" in packet.constraints

    def test_packet_serializes_to_json(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        raw = packet.model_dump_json()
        restored = HandoffPacket.model_validate_json(raw)
        assert restored.packet_id == packet.packet_id
        assert restored.hmac_token == packet.hmac_token


# ─────────────────────────────────────────────────────────────────────────────
# HandoffProtocol — create + verify
# ─────────────────────────────────────────────────────────────────────────────


class TestHandoffProtocol:
    def test_create_packet_passes_verification_gate(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        """create_packet must run the verifier and succeed."""
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert packet is not None

    def test_create_packet_fails_when_verifier_fails(
        self, task: Task, result: TaskResult, failing_protocol: HandoffProtocol
    ) -> None:
        """If verification fails, create_packet raises HandoffVerificationFailed."""
        with pytest.raises(HandoffVerificationFailed, match=task.id):
            failing_protocol.create_packet(task, result, source_agent_id="agent-A")

    def test_verify_packet_accepts_valid_packet(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        assert passing_protocol.verify_packet(packet)

    def test_verify_packet_rejects_tampered_hmac(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        tampered = packet.model_copy(update={"hmac_token": "a" * 64})
        with pytest.raises(HandoffIntegrityError):
            passing_protocol.verify_packet(tampered)

    def test_verify_packet_rejects_wrong_key(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        """A packet signed with a different key must fail verification."""
        other_protocol = HandoffProtocol(
            verifiers=[AlwaysPassVerifier()],
            secret_key=b"different-secret-key-32-bytes!!",
        )
        packet = passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        with pytest.raises(HandoffIntegrityError):
            other_protocol.verify_packet(packet)

    def test_handoff_audit_log_records_event(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        passing_protocol.create_packet(task, result, source_agent_id="agent-A")
        log = passing_protocol.audit_log
        assert len(log) == 1
        assert log[0]["task_id"] == task.id
        assert log[0]["event"] == "handoff_created"

    def test_failed_handoff_recorded_in_audit_log(
        self, task: Task, result: TaskResult, failing_protocol: HandoffProtocol
    ) -> None:
        with pytest.raises(HandoffVerificationFailed):
            failing_protocol.create_packet(task, result, source_agent_id="agent-A")
        log = failing_protocol.audit_log
        assert len(log) == 1
        assert log[0]["event"] == "handoff_blocked"


# ─────────────────────────────────────────────────────────────────────────────
# Conditional handoff
# ─────────────────────────────────────────────────────────────────────────────


class TestConditionalHandoff:
    def test_conditional_handoff_proceeds_when_condition_true(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        packet = passing_protocol.conditional_handoff(
            task,
            result,
            source_agent_id="agent-A",
            condition=lambda t, r: True,
        )
        assert packet is not None

    def test_conditional_handoff_blocked_when_condition_false(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        with pytest.raises(HandoffVerificationFailed, match="condition"):
            passing_protocol.conditional_handoff(
                task,
                result,
                source_agent_id="agent-A",
                condition=lambda t, r: False,
            )

    def test_conditional_handoff_blocked_even_if_verifier_passes(
        self, task: Task, result: TaskResult, passing_protocol: HandoffProtocol
    ) -> None:
        """Condition must be evaluated independently of verifier result."""
        with pytest.raises(HandoffVerificationFailed):
            passing_protocol.conditional_handoff(
                task,
                result,
                source_agent_id="agent-A",
                condition=lambda t, r: r.structured.get("amount", 0) > 999_999,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Verification history: no unverified state crosses boundaries
# ─────────────────────────────────────────────────────────────────────────────


class TestHandoffVerificationBoundary:
    def test_packet_records_all_verifier_results(
        self, task: Task, result: TaskResult, secret_key: bytes
    ) -> None:
        proto = HandoffProtocol(
            verifiers=[AlwaysPassVerifier(), AlwaysPassVerifier()],
            secret_key=secret_key,
        )
        packet = proto.create_packet(task, result, source_agent_id="agent-A")
        # Both verifiers ran → history should have 2 entries
        assert len(packet.verification_history) == 2

    def test_unverified_result_blocked(self, task: Task, secret_key: bytes) -> None:
        """A TaskResult where verified=False must be blocked by a strict verifier."""
        unverified = TaskResult(raw_output="Not yet verified", verified=False)
        proto = HandoffProtocol(
            verifiers=[AlwaysFailVerifier()],
            secret_key=secret_key,
        )
        with pytest.raises(HandoffVerificationFailed):
            proto.create_packet(task, unverified, source_agent_id="agent-A")
