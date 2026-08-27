"""The shortest path from `pip install` to a verified receipt.

Run it:

    python examples/gate_quickstart.py

It authorizes one payment behind a signed single-use permit, replays the exact
retry without producing a second effect, refuses an over-limit payment, and
verifies the resulting proof bundle offline.

`Gate.for_development` generates an ephemeral signing key so this file runs with
no setup. Nothing it signs can be verified by another process, and it must never
be used to authorize a real effect. A production gate takes an operator-owned
signer (KMS/HSM) and a durable permit store.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import Gate, GateDeniedError, check
from veridian.assurance import verify_proof_bundle


# --- the quickstart itself: everything below fits in about twenty lines --------
@check("amount_within_limit", config={"limit_minor": 100_000})
def amount_within_limit(ctx: object) -> bool:
    """Hard clause: refuse anything above USD 1,000.00."""
    return int(ctx.parameters["amount_minor"]) <= 100_000  # type: ignore[attr-defined]


@check("recipient_allowlisted")
def recipient_allowlisted(ctx: object) -> bool:
    """Hard clause: only settled account identifiers may receive funds."""
    return str(ctx.parameters["to"]).startswith("acct:")  # type: ignore[attr-defined]


def main() -> int:
    with TemporaryDirectory() as tmp:
        gate = Gate.for_development(
            audience="treasury-rail",
            checks=[amount_within_limit, recipient_allowlisted],
            store_path=Path(tmp) / "permits.db",
        )

        @gate.guard("payment.transfer", target=lambda to, **_: to)
        def transfer(*, to: str, amount_minor: int) -> str:
            """The credential holder. Runs only behind a verified ALLOW permit."""
            return f"rail-ref-{to}-{amount_minor}"

        outcome = transfer(to="acct:1234", amount_minor=25_000)
        # -------------------------------------------------------------------------

        # The exact retry redeems nothing: one permit, one economic effect.
        def must_not_run() -> str:
            raise AssertionError("a redeemed permit must never re-run the effect")

        replay = gate.execute(outcome.verdict, must_not_run)

        # A denial is a value with a reason, not a stack trace.
        try:
            transfer(to="acct:9", amount_minor=999_999)
        except GateDeniedError as denial:
            denied_reason = str(denial)
        else:  # pragma: no cover - the check above is deterministic
            raise AssertionError("an over-limit payment must be refused")

        # Anyone holding the public keys can check the proof without this process.
        proof = verify_proof_bundle(outcome.proof_bundle, gate.verification_keys)

        report = {
            "allowed": outcome.verdict.allowed,
            "effect_value": outcome.value,
            "receipt_id": outcome.receipt.receipt_id,
            "receipt_type": outcome.receipt.receipt_type.value,
            "retry": {
                "replayed": replay.replayed,
                "same_receipt": replay.receipt.digest == outcome.receipt.digest,
            },
            "denied": denied_reason,
            "proof": {
                "valid": proof.valid,
                # Reported honestly: a signature alone is not freshness, and a
                # local chain with no retained head is not append-only history.
                "replay_status": proof.replay_status.value,
                "history_status": proof.history_status.value,
            },
            "policy": {
                "contract_digest": gate.contract_digest,
                "clauses": [item.clause_id for item in outcome.verdict.clause_results],
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))

        assert outcome.verdict.allowed
        assert replay.replayed and replay.receipt.digest == outcome.receipt.digest
        assert proof.valid
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
