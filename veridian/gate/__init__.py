"""Porcelain over the assurance kernel: decide, permit, execute, attest.

This package adds no new trust properties. It composes the existing
``veridian.assurance`` and ``veridian.effects`` primitives into the shape most
callers need, and every artifact it emits — decision, permit, receipt, proof
bundle — is verifiable by the same offline verifier as a hand-built one.

    from veridian.gate import Gate, check

    @check("amount_within_limit")
    def amount_within_limit(ctx):
        return int(ctx.parameters["amount_minor"]) <= 100_000

    gate = Gate.for_development(
        audience="treasury-rail",
        checks=[amount_within_limit],
        store_path="permits.db",
    )

    @gate.guard("payment.transfer", target=lambda to, **_: to)
    def transfer(*, to: str, amount_minor: int) -> str:
        return f"sent {amount_minor} to {to}"

    outcome = transfer(to="acct:1234", amount_minor=25_000)

Use :meth:`Gate.for_development` only for exploration: it generates an ephemeral
signing key that no other process can verify. Production gates take an
operator-owned signer and a durable permit store.
"""

from ._check import Check, CheckContext, CheckOutcome, CheckPredicate, check
from ._errors import (
    GateConfigurationError,
    GateDeniedError,
    GateError,
    GateHeldError,
    GateRefusedError,
)
from ._gate import Gate, GateOutcome, Verdict

__all__ = [
    "Check",
    "CheckContext",
    "CheckOutcome",
    "CheckPredicate",
    "Gate",
    "GateConfigurationError",
    "GateDeniedError",
    "GateError",
    "GateHeldError",
    "GateOutcome",
    "GateRefusedError",
    "Verdict",
    "check",
]
