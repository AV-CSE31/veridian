# Threat model

The shortest honest answer to "should I depend on this?" — including where the
answer is no.

Veridian is alpha software. Passing tests and finite benchmarks are evidence for
the disclosed cases only. They are not proof of zero residual risk and not a
substitute for domain, security and cryptography review.

## The boundary

Veridian assumes an agent is **untrusted** and may propose any action, including
a malicious or subtly wrong one. It places a deterministic decision between the
proposal and the effect, and produces evidence afterwards.

```
untrusted agent  ──proposes──▶  [ Veridian ]  ──permit──▶  trusted executor  ──▶  world
                                     │                       (holds credentials)
                                     └──────── signed decision + receipt ────────▶ auditor
```

Three separations do the work:

1. **Semantics from transport.** What the action *means* determines the digest a
   permit binds. Which protocol carried it is recorded separately in a
   `TransportBinding` and never affects authorization.
2. **Decision from execution.** The component that decides holds no credentials.
   The component that holds credentials makes no decisions — it redeems a permit
   or refuses.
3. **Signing from proposing.** The agent never holds a signing key. If it did,
   every property below collapses.

## What Veridian is trusted to do

| Property | Mechanism | Bound |
|---|---|---|
| A denial cannot become a permit | `aggregate_disposition` is fail-closed; permits mint on `ALLOW` only | Holds for hard clauses. Soft clauses are advisory by construction. |
| An approved action cannot be mutated before execution | The permit binds `semantic_digest`; the executor re-derives it from the action it was handed | Byte-exact. A changed amount is a different digest and is refused. |
| One permit yields one redemption | Atomic SQLite redemption, `max_uses=1`, durable outbox | Single host. Not distributed consensus. |
| A receipt identifies exactly what happened | Ed25519 over canonical bytes, binding permit, outbox, result and external reference | Assumes the receipt signer is honest. |
| An auditor can check a decision offline | `verify_proof_bundle` re-derives every disclosed digest | Signature and binding only — see below. |
| A decision names the code that made it | Each `ClauseResultV1` carries a `verifier_manifest_digest` binding verifier id, version, config, source and runtime | For gate checks, source binding is best-effort; the manifest records `source_bound: false` when source is unavailable. |

## What Veridian cannot protect against

These are not bugs to be fixed later. They are outside the boundary by
construction, and any of them defeats the whole chain:

- **A compromised signer.** Anyone holding the permit or receipt key can mint
  authority. Veridian ships no fallback key and no key management; use a KMS or
  HSM and rotate.
- **A compromised trusted executor.** It holds the credentials. If it lies about
  dispatching, the receipt attests a lie in good faith.
- **A compromised evidence producer.** Veridian binds a *reference* to evidence
  and its declared trust class. It cannot tell you the sanctions feed was
  correct — only which feed was cited and when it was observed.
- **A compromised anchor or witness.** Without an independently retained head,
  history is `unanchored` and a valid rollback, fork or suffix truncation is
  undetectable.
- **A malicious verifier implementation.** `IsolatedVerifierRunner` is a bounded
  subprocess protocol, **not an OS security sandbox**. It bounds accidents, not
  adversaries.
- **A downstream adapter that ignores idempotency.** Veridian guarantees one
  redemption per permit. Exactly-once *economic* effect additionally requires the
  payment API to honour the supplied idempotency key.
- **Prompt injection reaching the policy.** Checks must be deterministic
  functions of the action and the state snapshot. A check that consults an LLM
  imports that model's failure modes into your authorization decision.

## What offline verification does and does not establish

`verify_proof_bundle` with no context returns `valid=True` alongside
`replay_status='not-checked'` and `history_status='unanchored'`. That is the
correct, conservative reading:

- **Established:** the bytes were signed by a key you trust, and every disclosed
  digest binding is internally consistent.
- **Not established:** that this decision is *fresh* (no authoritative nonce
  registry or clock was supplied), or that the receipt chain has not been
  rewritten (no independently retained head or witness was supplied).

Supply a `ProofVerificationContext` with a `NonceRegistry` and an `AnchorContext`
to upgrade both. Retain the head **outside the system being audited** — a chain
that vouches for itself vouches for nothing.

## Legacy HMAC paths

`verify_completion(...)` and the JSONL report chain use operator-supplied HMAC
keys. HMAC is symmetric: **any holder of the key can forge or rewrite a valid
chain.** These paths exist for compatibility. Prefer the Ed25519 assurance
kernel for anything an independent party must check.

Historical `verification-report.v1` archives were hash-chained but unsigned.
They fail closed unless an auditor opts in explicitly with a trusted head, and
the returned limitations always identify the unsigned records.

## Deliberate non-goals

Veridian `0.4.0` is not a managed control plane, a distributed ledger, a hosted
PR reviewer, a general policy DSL, an identity issuer, a production RTGS
connector, or an OS-level sandbox. The banking and deployment packs are
synthetic: they use no credentials, no network and no production data, and
authorization is not a claim about final settlement.

## Reporting

See [SECURITY.md](../SECURITY.md). Use GitHub private vulnerability reporting;
do not put exploit details, credentials or real proof bundles in a public issue.
