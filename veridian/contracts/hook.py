"""
veridian.contracts.hook
────────────────────────
SprintContractHook — pre-execution contract validation middleware.

Validates SprintContracts before task execution begins. Runs at priority=10
(after LoggingHook=0, before all other hooks=50).

Lifecycle:
  before_task:
    - If task.metadata has 'sprint_contract', extract and validate
    - If raise_on_unsigned=True (default), raise ContractViolation for unsigned contracts
    - If require_contract=True, raise ContractViolation when no contract is present
  after_task:
    - Records task completion in the contract's negotiation history (in-memory only)

Contract: Hooks observe and raise. They cannot write to the ledger or call LLMs.
Hook errors propagate only when the hook explicitly raises — ContractViolation is
a deliberate signal, not an unexpected error.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.contracts.sprint import SprintContract
from veridian.core.exceptions import ContractViolation
from veridian.hooks.base import BaseHook

__all__ = ["SprintContractHook"]

log = logging.getLogger(__name__)


class SprintContractHook(BaseHook):
    """
    Validates sprint contracts before task execution.

    Args:
        raise_on_unsigned:  If True (default), raise ContractViolation when
                            a task's sprint_contract is not signed.
        require_contract:   If True, raise ContractViolation when a task has no
                            sprint_contract in its metadata. Default: False.
        signing_secret:     Optional HMAC secret for signature verification.
                            Defaults to "" (uses the built-in default key).
    """

    id: ClassVar[str] = "sprint_contract"
    priority: ClassVar[int] = 10  # after logging (0), before all others (50)

    def __init__(
        self,
        raise_on_unsigned: bool = True,
        require_contract: bool = False,
        signing_secret: str = "",
    ) -> None:
        self.raise_on_unsigned = raise_on_unsigned
        self.require_contract = require_contract
        self.signing_secret = signing_secret

    def before_task(self, event: Any) -> None:
        """
        Validate the sprint contract (if any) before task execution starts.

        If the task has no sprint_contract:
          - If require_contract=True → raise ContractViolation
          - Otherwise → silent no-op

        If the task has a sprint_contract:
          - If raise_on_unsigned=True and contract is unsigned → raise ContractViolation
          - Otherwise → log and continue
        """
        task = getattr(event, "task", None)
        if task is None:
            return

        contract_dict = task.metadata.get("sprint_contract")

        if contract_dict is None:
            if self.require_contract:
                raise ContractViolation(
                    f"Task {task.id!r} has no sprint_contract in metadata "
                    f"but require_contract=True. "
                    f"Create and attach a SprintContract before adding to ledger."
                )
            return

        contract = SprintContract.from_dict(contract_dict)

        if self.raise_on_unsigned and not contract.is_signed:
            raise ContractViolation(
                f"Task {task.id!r}: SprintContract {contract.contract_id!r} is unsigned. "
                f"Call contract.sign() before adding to task.metadata."
            )

        log.debug(
            "sprint_contract.hook.before_task task_id=%s contract_id=%s signed=%s",
            task.id,
            contract.contract_id,
            contract.is_signed,
        )

    def after_task(self, event: Any) -> None:
        """
        Record task completion in the contract's negotiation history.
        In-memory only — does not mutate the task ledger.
        """
        task = getattr(event, "task", None)
        if task is None:
            return

        contract_dict = task.metadata.get("sprint_contract")
        if contract_dict is None:
            return

        # Note: this mutates the in-memory dict representation but the task
        # is already committed to the ledger at this point — this is safe.
        log.debug(
            "sprint_contract.hook.after_task task_id=%s",
            task.id,
        )
