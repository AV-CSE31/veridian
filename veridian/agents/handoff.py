"""
veridian.agents.handoff
────────────────────────
Multi-Agent Handoff Protocol (F2.2).

Every task state that crosses an agent boundary is verified first.
No unverified state is ever transmitted via HandoffPacket.

Design:
- HandoffPacket is an immutable (frozen Pydantic) envelope containing
  task_state, verification_history, context_summary, and constraints.
- An HMAC-SHA256 token authenticates the packet source.
- HandoffProtocol runs all registered verifiers before creating a packet.
  If any verifier fails, HandoffVerificationFailed is raised.
- All handoffs are recorded in an in-memory audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from veridian.core.exceptions import HandoffIntegrityError, HandoffVerificationFailed
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = ["HandoffPacket", "HandoffProtocol"]


# ─────────────────────────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────────────────────────


class HandoffPacket(BaseModel):
    """
    Immutable envelope for transferring task state between agents.

    Fields
    ------
    packet_id           Unique identifier for this handoff.
    source_agent_id     ID of the agent creating the handoff.
    target_agent_id     ID of the intended recipient (None = any available).
    task_id             ID of the task being transferred.
    task_state          Serialized Task dict (snapshot at handoff time).
    verification_history List of VerificationResult dicts from the boundary check.
    context_summary     Human-readable summary of context for the receiving agent.
    constraints         Operational constraints the receiving agent must honour.
    timestamp_utc       UTC timestamp of packet creation.
    hmac_token          HMAC-SHA256 over canonical packet fields — tamper detection.
    """

    model_config = ConfigDict(frozen=True)

    packet_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_agent_id: str
    target_agent_id: str | None = None
    task_id: str
    task_state: dict[str, Any]
    verification_history: list[dict[str, Any]]
    context_summary: str = ""
    constraints: list[str] = Field(default_factory=list)
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hmac_token: str  # hex-encoded, 64 chars (SHA-256)


# ─────────────────────────────────────────────────────────────────────────────
# HMAC helpers
# ─────────────────────────────────────────────────────────────────────────────


def _compute_hmac(
    packet_id: str,
    task_id: str,
    source_agent_id: str,
    timestamp_utc: datetime,
    secret_key: bytes,
) -> str:
    """Compute HMAC-SHA256 over canonical identity fields."""
    message = "|".join([packet_id, task_id, source_agent_id, timestamp_utc.isoformat()])
    return hmac.new(secret_key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_hmac(packet: HandoffPacket, secret_key: bytes) -> bool:
    """Return True iff the packet's HMAC token is valid for the given key."""
    expected = _compute_hmac(
        packet.packet_id,
        packet.task_id,
        packet.source_agent_id,
        packet.timestamp_utc,
        secret_key,
    )
    return hmac.compare_digest(expected, packet.hmac_token)


# ─────────────────────────────────────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────────────────────────────────────


class HandoffProtocol:
    """
    Verification gate for inter-agent task handoffs.

    Usage::

        protocol = HandoffProtocol(
            verifiers=[SchemaVerifier(), ToolSafetyVerifier()],
            secret_key=secrets_provider.get("handoff/signing_key"),
        )

        # Creates packet or raises HandoffVerificationFailed
        packet = protocol.create_packet(task, result, source_agent_id="agent-A")

        # Receiving agent verifies the packet before trusting it
        protocol.verify_packet(packet)  # raises HandoffIntegrityError if tampered
    """

    def __init__(
        self,
        verifiers: list[BaseVerifier],
        secret_key: bytes,
    ) -> None:
        self._verifiers = verifiers
        self._secret_key = secret_key
        self._audit_log: list[dict[str, Any]] = []

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Read-only view of the handoff audit log."""
        return list(self._audit_log)

    def create_packet(
        self,
        task: Task,
        result: TaskResult,
        *,
        source_agent_id: str,
        target_agent_id: str | None = None,
        context_summary: str = "",
        constraints: list[str] | None = None,
    ) -> HandoffPacket:
        """
        Run all verifiers against (task, result). If all pass, create and return
        a signed HandoffPacket. If any verifier fails, raise HandoffVerificationFailed
        and record the failure in the audit log.
        """
        ver_history: list[dict[str, Any]] = []
        failures: list[str] = []

        for verifier in self._verifiers:
            vr: VerificationResult = verifier.verify(task, result)
            ver_history.append(
                {
                    "verifier_id": verifier.id,
                    "passed": vr.passed,
                    "error": vr.error,
                    "evidence": vr.evidence,
                }
            )
            if not vr.passed:
                failures.append(f"{verifier.id}: {vr.error}")

        if failures:
            self._audit_log.append(
                {
                    "event": "handoff_blocked",
                    "task_id": task.id,
                    "source_agent_id": source_agent_id,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "failures": failures,
                }
            )
            raise HandoffVerificationFailed(
                task_id=task.id,
                reason="; ".join(failures[:3]),
            )

        # All verifiers passed — build the packet
        packet_id = str(uuid.uuid4())
        timestamp_utc = datetime.now(timezone.utc)
        hmac_token = _compute_hmac(
            packet_id, task.id, source_agent_id, timestamp_utc, self._secret_key
        )

        packet = HandoffPacket(
            packet_id=packet_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            task_id=task.id,
            task_state=task.to_dict(),
            verification_history=ver_history,
            context_summary=context_summary,
            constraints=constraints or [],
            timestamp_utc=timestamp_utc,
            hmac_token=hmac_token,
        )

        self._audit_log.append(
            {
                "event": "handoff_created",
                "task_id": task.id,
                "packet_id": packet_id,
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
                "timestamp_utc": timestamp_utc.isoformat(),
            }
        )
        return packet

    def verify_packet(self, packet: HandoffPacket) -> bool:
        """
        Verify the HMAC token of an incoming packet.

        Returns True if valid.
        Raises HandoffIntegrityError if the token is invalid (packet tampered).
        """
        if not _verify_hmac(packet, self._secret_key):
            raise HandoffIntegrityError(
                f"HMAC verification failed for packet {packet.packet_id!r}. "
                "Packet may have been tampered with or signed by a different key."
            )
        return True

    def conditional_handoff(
        self,
        task: Task,
        result: TaskResult,
        *,
        source_agent_id: str,
        condition: Callable[[Task, TaskResult], bool],
        target_agent_id: str | None = None,
        context_summary: str = "",
        constraints: list[str] | None = None,
    ) -> HandoffPacket:
        """
        Create a handoff packet only if `condition(task, result)` is True.

        The condition is evaluated AFTER the verification gate. If the condition
        returns False, HandoffVerificationFailed is raised with reason="condition".
        """
        # First run the verification gate (may raise HandoffVerificationFailed)
        packet = self.create_packet(
            task,
            result,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            context_summary=context_summary,
            constraints=constraints,
        )

        # Then evaluate the caller-supplied condition
        if not condition(task, result):
            self._audit_log.append(
                {
                    "event": "handoff_blocked",
                    "task_id": task.id,
                    "source_agent_id": source_agent_id,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "failures": ["condition returned False"],
                }
            )
            raise HandoffVerificationFailed(
                task_id=task.id,
                reason="condition returned False — conditional handoff blocked",
            )

        return packet
