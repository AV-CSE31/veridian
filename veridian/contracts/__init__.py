"""
veridian.contracts
──────────────────
Sprint Contract Protocol — pre-execution commitment verification.

A SprintContract is created and signed before task execution begins. It defines:
  - deliverables:         what the agent commits to produce
  - success_criteria:     deterministic conditions that define done
  - test_conditions:      specific test conditions to satisfy
  - acceptance_threshold: minimum pass score (0.0–1.0)

The contract is HMAC-SHA256 signed and attached to task.metadata["sprint_contract"]
before the task enters the ledger. SprintContractHook validates the signature
pre-execution. SprintContractVerifier checks the result score post-execution.

Quick start::

    from veridian.contracts import SprintContract, SprintContractHook, SprintContractVerifier

    contract = SprintContract.create(
        task_id=task.id,
        deliverables=["migration_report.md", "all tests passing"],
        success_criteria=["pytest exit 0", "mypy strict clean"],
        acceptance_threshold=0.9,
    ).sign()

    task.metadata["sprint_contract"] = contract.to_dict()
    runner.add_hook(SprintContractHook())
    # Use verifier_id="sprint_contract" on the task
"""

from __future__ import annotations

from veridian.contracts.hook import SprintContractHook
from veridian.contracts.sprint import ContractRegistry, SprintContract
from veridian.contracts.verifier import SprintContractVerifier
from veridian.core.exceptions import ContractNotFound, ContractViolation

# Auto-register the sprint_contract verifier so verifier_id="sprint_contract" works
from veridian.verify.base import registry as _verifier_registry

_verifier_registry.register(SprintContractVerifier)

__all__ = [
    "SprintContract",
    "ContractRegistry",
    "SprintContractVerifier",
    "SprintContractHook",
    "ContractViolation",
    "ContractNotFound",
]
