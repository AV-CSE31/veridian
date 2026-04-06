"""
veridian.contracts.sprint
─────────────────────────
Sprint Contract Protocol — pre-execution commitment between generator and evaluator.

A SprintContract is created before task execution. It contains:
  - deliverables:         what the agent commits to produce
  - success_criteria:     deterministic conditions that define done
  - test_conditions:      specific test conditions to satisfy
  - acceptance_threshold: minimum score (0.0–1.0) required to pass

The contract is HMAC-SHA256 signed before execution begins and becomes part of
the provenance chain, transforming Veridian from post-hoc validation into
pre-execution commitment verification.

Usage::

    contract = SprintContract.create(
        task_id=task.id,
        deliverables=["migration_report.md", "tests passing"],
        success_criteria=["pytest exit code 0", "no type errors"],
        acceptance_threshold=0.9,
    ).sign()

    task.metadata["sprint_contract"] = contract.to_dict()
    runner.run()  # SprintContractHook validates pre-execution

Reference: DEEP_RESEARCH_REPORT.md §3 (Sprint Contract Protocol, Tier B1)
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from veridian.core.exceptions import ContractNotFound, ContractViolation

__all__ = ["SprintContract", "ContractRegistry"]

log = logging.getLogger(__name__)


@dataclass
class SprintContract:
    """
    Pre-execution commitment contract for a single task.

    Lifecycle::

        CREATED (unsigned) ──sign()──► SIGNED ──(execution)──► verified by SprintContractVerifier

    Invariants:
      - contract_id is globally unique (sc_ prefix + 12-char hex)
      - signature is HMAC-SHA256 over canonical JSON of core fields
      - verify_signature() returns False if any core field is tampered after signing
      - negotiation_history is an append-only audit log
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    contract_id: str = field(default_factory=lambda: f"sc_{uuid.uuid4().hex[:12]}")
    task_id: str = ""

    # ── Contract terms ────────────────────────────────────────────────────────
    deliverables: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    test_conditions: list[str] = field(default_factory=list)
    acceptance_threshold: float = 0.8  # 0.0–1.0; result score must meet this

    # ── Provenance ────────────────────────────────────────────────────────────
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    signed_at: datetime | None = None
    signature: str | None = None  # HMAC-SHA256 hex digest

    # ── Negotiation history (append-only audit log) ───────────────────────────
    negotiation_history: list[dict[str, Any]] = field(default_factory=list)

    # ─────────────────────────────────────────────────────────────────────────
    # Factory
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        task_id: str,
        deliverables: list[str],
        success_criteria: list[str],
        test_conditions: list[str] | None = None,
        acceptance_threshold: float = 0.8,
    ) -> SprintContract:
        """
        Create an unsigned SprintContract. Validates all required fields.

        Raises:
            ContractViolation: if deliverables or success_criteria are empty,
                               or if acceptance_threshold is outside [0.0, 1.0].
        """
        if not deliverables:
            raise ContractViolation(
                "SprintContract.deliverables must not be empty — "
                "specify at least one deliverable the agent commits to produce."
            )
        if not success_criteria:
            raise ContractViolation(
                "SprintContract.success_criteria must not be empty — "
                "specify at least one deterministic criterion that defines done."
            )
        if not 0.0 <= acceptance_threshold <= 1.0:
            raise ContractViolation(
                f"SprintContract.acceptance_threshold must be in [0.0, 1.0], "
                f"got {acceptance_threshold}."
            )
        return cls(
            task_id=task_id,
            deliverables=list(deliverables),
            success_criteria=list(success_criteria),
            test_conditions=list(test_conditions or []),
            acceptance_threshold=acceptance_threshold,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Signing
    # ─────────────────────────────────────────────────────────────────────────

    def _canonical_bytes(self) -> bytes:
        """
        Deterministic JSON serialization of the core contract fields.
        Order is sorted to ensure canonical form regardless of insertion order.
        Only fields that define the contractual obligation are included —
        created_at, signed_at, and negotiation_history are excluded.
        """
        payload = {
            "contract_id": self.contract_id,
            "task_id": self.task_id,
            "deliverables": sorted(self.deliverables),
            "success_criteria": sorted(self.success_criteria),
            "test_conditions": sorted(self.test_conditions),
            "acceptance_threshold": self.acceptance_threshold,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def sign(self, secret: str = "") -> SprintContract:
        """
        Sign the contract with HMAC-SHA256.

        The signature covers all core contract terms. Any modification to
        deliverables, success_criteria, test_conditions, or acceptance_threshold
        after signing will invalidate the signature.

        Returns self to allow chaining:  contract = SprintContract.create(...).sign()
        """
        key = secret.encode() if secret else b"veridian-sprint-contract"
        sig = _hmac.new(key, self._canonical_bytes(), hashlib.sha256).hexdigest()
        self.signature = sig
        self.signed_at = datetime.now(tz=UTC)
        log.debug(
            "contract.signed contract_id=%s task_id=%s",
            self.contract_id,
            self.task_id,
        )
        return self

    def verify_signature(self, secret: str = "") -> bool:
        """
        Return True if the stored signature is valid for current contract content.
        Returns False if unsigned, or if any signed field was tampered with.
        """
        if self.signature is None:
            return False
        key = secret.encode() if secret else b"veridian-sprint-contract"
        expected = _hmac.new(key, self._canonical_bytes(), hashlib.sha256).hexdigest()
        return _hmac.compare_digest(self.signature, expected)

    @property
    def is_signed(self) -> bool:
        """True if the contract has been signed (signature is not None)."""
        return self.signature is not None

    # ─────────────────────────────────────────────────────────────────────────
    # Negotiation history
    # ─────────────────────────────────────────────────────────────────────────

    def add_negotiation_note(self, agent: str, note: str) -> None:
        """
        Append a negotiation note to the audit log.
        This does NOT invalidate the signature — history is excluded from signing.
        """
        self.negotiation_history.append(
            {
                "agent": agent,
                "note": note,
                "ts": datetime.now(tz=UTC).isoformat(),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "task_id": self.task_id,
            "deliverables": self.deliverables,
            "success_criteria": self.success_criteria,
            "test_conditions": self.test_conditions,
            "acceptance_threshold": self.acceptance_threshold,
            "created_at": self.created_at.isoformat(),
            "signed_at": self.signed_at.isoformat() if self.signed_at else None,
            "signature": self.signature,
            "negotiation_history": self.negotiation_history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SprintContract:
        c = cls(
            contract_id=d["contract_id"],
            task_id=d.get("task_id", ""),
            deliverables=d.get("deliverables", []),
            success_criteria=d.get("success_criteria", []),
            test_conditions=d.get("test_conditions", []),
            acceptance_threshold=d.get("acceptance_threshold", 0.8),
            signature=d.get("signature"),
            negotiation_history=d.get("negotiation_history", []),
        )
        if d.get("created_at"):
            c.created_at = datetime.fromisoformat(d["created_at"])
        if d.get("signed_at"):
            c.signed_at = datetime.fromisoformat(d["signed_at"])
        return c

    def __repr__(self) -> str:
        signed = "signed" if self.is_signed else "unsigned"
        return (
            f"SprintContract(id={self.contract_id!r}, task_id={self.task_id!r}, "
            f"deliverables={len(self.deliverables)}, criteria={len(self.success_criteria)}, "
            f"threshold={self.acceptance_threshold}, {signed})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


class ContractRegistry:
    """
    In-process registry for SprintContracts keyed by contract_id.

    Used to look up contracts during execution without passing them through
    every call site. Typically one registry per runner run.
    """

    def __init__(self) -> None:
        self._contracts: dict[str, SprintContract] = {}

    def register(self, contract: SprintContract) -> None:
        """Add or overwrite a contract. Overwrites silently (last-write wins)."""
        self._contracts[contract.contract_id] = contract
        log.debug("contract_registry.register contract_id=%s", contract.contract_id)

    def get(self, contract_id: str) -> SprintContract:
        """
        Return the contract for the given ID.

        Raises:
            ContractNotFound: if contract_id is not registered.
        """
        c = self._contracts.get(contract_id)
        if c is None:
            raise ContractNotFound(
                f"Contract {contract_id!r} not found in ContractRegistry. "
                f"Call registry.register(contract) before referencing it."
            )
        return c

    def list_all(self) -> list[SprintContract]:
        """Return all registered contracts (order not guaranteed)."""
        return list(self._contracts.values())
