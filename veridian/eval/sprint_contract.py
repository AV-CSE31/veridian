"""
veridian.eval.sprint_contract
──────────────────────────────
SprintContract — pre-execution commitment between the generator and evaluator.

A SprintContract is negotiated before the generator runs. It defines:
  - deliverables: what the generator must produce
  - success_criteria: how success is measured
  - test_conditions: specific test cases that must pass
  - evaluation_threshold: minimum aggregate score to be accepted

Both generator and evaluator must sign before the VerificationPipeline will
execute. The contract becomes part of the provenance chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from veridian.core.exceptions import ContractViolation

__all__ = ["SprintContract"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class SprintContract:
    """
    Pre-execution contract between generator and adversarial evaluator.

    Must be signed by both parties (sign_generator() + sign_evaluator())
    before VerificationPipeline.run() will accept it.
    """

    task_id: str
    deliverables: list[str]
    success_criteria: list[str]
    test_conditions: list[str]
    evaluation_threshold: float
    contract_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    signed_by_generator: bool = False
    signed_by_evaluator: bool = False

    def __post_init__(self) -> None:
        if not self.deliverables:
            raise ContractViolation(
                contract_id=self.contract_id,
                reason="deliverables must be a non-empty list",
            )
        if not (0.0 < self.evaluation_threshold <= 1.0):
            raise ContractViolation(
                contract_id=self.contract_id,
                reason=(
                    f"evaluation_threshold must be in (0.0, 1.0], got {self.evaluation_threshold}"
                ),
            )

    def sign_generator(self) -> None:
        """Mark the generator as having accepted this contract."""
        self.signed_by_generator = True

    def sign_evaluator(self) -> None:
        """Mark the evaluator as having accepted this contract."""
        self.signed_by_evaluator = True

    def is_signed(self) -> bool:
        """Return True only when both generator and evaluator have signed."""
        return self.signed_by_generator and self.signed_by_evaluator

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (used in provenance chain)."""
        return {
            "contract_id": self.contract_id,
            "task_id": self.task_id,
            "deliverables": self.deliverables,
            "success_criteria": self.success_criteria,
            "test_conditions": self.test_conditions,
            "evaluation_threshold": self.evaluation_threshold,
            "created_at": self.created_at.isoformat(),
            "signed_by_generator": self.signed_by_generator,
            "signed_by_evaluator": self.signed_by_evaluator,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SprintContract:
        """Deserialize from a dict (e.g. loaded from ledger or audit trail)."""
        created_at = datetime.fromisoformat(data["created_at"])
        # Build with contract_id provided (preserves original ID)
        obj = cls.__new__(cls)
        object.__setattr__(obj, "contract_id", data["contract_id"])
        object.__setattr__(obj, "task_id", data["task_id"])
        object.__setattr__(obj, "deliverables", data["deliverables"])
        object.__setattr__(obj, "success_criteria", data["success_criteria"])
        object.__setattr__(obj, "test_conditions", data["test_conditions"])
        object.__setattr__(obj, "evaluation_threshold", data["evaluation_threshold"])
        object.__setattr__(obj, "created_at", created_at)
        object.__setattr__(obj, "signed_by_generator", data.get("signed_by_generator", False))
        object.__setattr__(obj, "signed_by_evaluator", data.get("signed_by_evaluator", False))
        return obj
