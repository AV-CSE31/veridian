# Quickstart

From install to a signed, independently verifiable receipt.

```bash
pip install veridian-ai
```

## The whole thing

```python
from veridian import Gate, GateDeniedError, check

@check("amount_within_limit", config={"limit_minor": 100_000})
def amount_within_limit(ctx):
    return int(ctx.parameters["amount_minor"]) <= 100_000

@check("recipient_allowlisted")
def recipient_allowlisted(ctx):
    return str(ctx.parameters["to"]).startswith("acct:")

gate = Gate.for_development(
    audience="treasury-rail",
    checks=[amount_within_limit, recipient_allowlisted],
    store_path="permits.db",
)

@gate.guard("payment.transfer", target=lambda to, **_: to)
def transfer(*, to: str, amount_minor: int) -> str:
    return f"rail-ref-{to}-{amount_minor}"

outcome = transfer(to="acct:1234", amount_minor=25_000)
print(outcome.value, outcome.receipt.receipt_id)
```

`examples/gate_quickstart.py` is this program plus assertions.

## What just happened

The decorated function did not run until every hard clause passed. Before it
ran, Veridian:

1. normalized the call into `ActionSemanticsV1` — business meaning separate from
   how the call arrived;
2. bound principal, audience, purpose, nonce, validity window, state and policy
   into an `AuthorizationEnvelope`;
3. evaluated each check and recorded a `ClauseResultV1` carrying the digest of
   the exact code and configuration that decided it;
4. aggregated fail-closed into `ALLOW` / `DENY` / `HOLD`;
5. minted an Ed25519-signed, single-use `ExecutionPermitV1` — on `ALLOW` only;
6. redeemed that permit atomically through a SQLite outbox, dispatched, and
   signed an `EffectReceiptV1` over the result.

Every one of those is an ordinary `veridian.assurance` / `veridian.effects`
value. `Gate` is porcelain over primitives you can also build by hand; it
introduces no trust properties of its own.

## Three properties worth checking yourself

### A denial is a value, not a crash

```python
verdict = gate.evaluate(
    action="payment.transfer",
    target="acct:9",
    parameters={"to": "acct:9", "amount_minor": 999_999},
)
verdict.disposition        # Disposition.DENY
verdict.reason()           # 'deny: amount_within_limit=VIOLATED'
verdict.signed_permit      # None — no permit is minted for a denial
```

The `@guard` decorator raises `GateDeniedError` / `GateHeldError` instead, and
the exception carries `.verdict` so the caller can inspect the clauses.

### Uncertainty holds; it never allows

A check that raises, returns a non-boolean, or explicitly returns `UNKNOWN`
produces `HOLD`, not `ALLOW`:

```python
@check("sanctions_screening")
def sanctions_screening(ctx):
    if "screening" not in ctx.state:
        return CheckOutcome(status=ClauseStatus.UNKNOWN, reason_code="FEED_UNAVAILABLE")
    return ctx.state["screening"]["clear"] is True
```

This is the whole point of the algebra: a missing sanctions feed must not read
as a clean one.

### One permit, one effect

```python
first  = gate.execute(verdict, do_the_thing)   # replayed=False, runs the callable
second = gate.execute(verdict, do_the_thing)   # replayed=True, does NOT run it
assert second.receipt.digest == first.receipt.digest
```

Exactly-once is a property of the durable store, not of process memory — a
different process pointed at the same `store_path` replays identically.

Exactly-once *economic* effects additionally require your downstream adapter to
honour the supplied idempotency key. Veridian guarantees one redemption per
permit; it cannot guarantee your payment API does the same.

## Verifying a proof somewhere else

```python
from veridian.assurance import verify_proof_bundle

result = verify_proof_bundle(outcome.proof_bundle, gate.verification_keys)
result.valid            # True
result.replay_status    # 'not-checked'
result.history_status   # 'unanchored'
```

Those last two are not defects. A valid signature proves who signed what; it
does not prove freshness or that no history was rewritten. To upgrade them,
supply a `ProofVerificationContext` with a nonce registry and an independently
retained anchor head. See [proof-format.md](proof-format.md).

## Going to production

`Gate.for_development` generates an ephemeral key that exists only inside the
process. Nothing it signs can be verified anywhere else. A real gate looks like:

```python
gate = Gate(
    audience="treasury-rail",
    principal="agent://payments-bot",
    purpose="vendor-payout",
    checks=[...],
    signer=my_kms_signer,               # implements veridian.assurance.Signer
    permit_keys=my_public_key_provider,
    receipt_signer=my_receipt_signer,
    receipt_keys=my_receipt_key_provider,
    store_path="/var/lib/veridian/permits.db",
    permit_ttl_seconds=120,
)
```

Before you rely on it, read [threat-model.md](threat-model.md) — particularly
the list of things Veridian cannot protect against.
