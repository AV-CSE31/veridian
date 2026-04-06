"""
veridian.contracts.verifier
────────────────────────────
SprintContractVerifier — post-execution verification of sprint contract terms.

Registered as verifier_id="sprint_contract" in the VerifierRegistry.
Reads the contract from task.metadata["sprint_contract"] and checks:
  1. Contract is present in task.metadata
  2. Contract is signed (if require_signed=True, the default)
  3. result.structured.get("contract_score", 1.0) >= contract.acceptance_threshold

The contract_score field (0.0–1.0) in the result allows the WorkerAgent to
self-report a completion score. If absent, a score of 1.0 is assumed (full pass).

Usage::

    task = Task(
        title="Migrate auth module",
        verifier_id="sprint_contract",
        metadata={"sprint_contract": contract.to_dict()},
    )
    # WorkerAgent should include in <veridian:result>:
    #   {"contract_score": 0.92, ...}

Rules (per CLAUDE.md):
  - NEVER calls an LLM
  - Stateless — no instance-level mutable state
  - Error messages: specific, actionable, ≤ 300 chars
"""

from __future__ import annotations

import logging
from typing import ClassVar

from veridian.contracts.sprint import SprintContract
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = ["SprintContractVerifier"]

log = logging.getLogger(__name__)


class SprintContractVerifier(BaseVerifier):
    """
    Verifies task result against a SprintContract embedded in task.metadata.

    Config (pass as verifier_config on the Task):
      require_signed (bool):  default True  — reject unsigned contracts
    """

    id: ClassVar[str] = "sprint_contract"
    description: ClassVar[str] = (
        "Verifies result satisfies a SprintContract: checks signature and "
        "that result.structured['contract_score'] meets acceptance_threshold."
    )

    def __init__(self, require_signed: bool = True) -> None:
        self.require_signed = require_signed

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """
        Run sprint contract verification.

        Returns:
            VerificationResult(passed=True, evidence={...})  on success
            VerificationResult(passed=False, error=...)      on failure
        """
        # ── 1. Extract contract from task metadata ────────────────────────
        contract_dict = task.metadata.get("sprint_contract")
        if contract_dict is None:
            return VerificationResult(
                passed=False,
                error=(
                    "No sprint_contract in task.metadata. "
                    "Attach with: task.metadata['sprint_contract'] = contract.to_dict()"
                )[:300],
            )

        contract = SprintContract.from_dict(contract_dict)

        # ── 2. Check signature ────────────────────────────────────────────
        if self.require_signed and not contract.is_signed:
            return VerificationResult(
                passed=False,
                error=(
                    f"SprintContract {contract.contract_id!r} is unsigned. "
                    f"Call contract.sign() before adding to task.metadata."
                )[:300],
            )

        # ── 3. Check result score against acceptance threshold ────────────
        score: float = float(result.structured.get("contract_score", 1.0))
        if score < contract.acceptance_threshold:
            return VerificationResult(
                passed=False,
                error=(
                    f"Contract {contract.contract_id!r}: score {score:.2f} < "
                    f"threshold {contract.acceptance_threshold:.2f}. "
                    f"Improve output quality and re-submit."
                )[:300],
            )

        log.debug(
            "sprint_contract.verify.passed contract_id=%s score=%.2f threshold=%.2f",
            contract.contract_id,
            score,
            contract.acceptance_threshold,
        )
        return VerificationResult(
            passed=True,
            evidence={
                "contract_id": contract.contract_id,
                "signed": contract.is_signed,
                "score": score,
                "acceptance_threshold": contract.acceptance_threshold,
                "deliverables_count": len(contract.deliverables),
                "criteria_count": len(contract.success_criteria),
            },
        )
