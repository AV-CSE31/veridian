# Proof format

What is inside a Veridian proof bundle, and how an independent party checks one.

This document is the durable artifact. If the library were abandoned tomorrow,
a proof bundle written today would remain checkable from this specification
alone, using only Ed25519 and SHA-256.

## Canonical JSON Profile v1

Every digest in Veridian is `sha256` over bytes produced by **Veridian Canonical
JSON Profile v1** (`veridian.cjson-sha256.v1`). The profile accepts strictly
less than general JSON so that one value has exactly one byte encoding:

| Rule | Value |
|---|---|
| Types | `null`, boolean, integer, string, array, object with string keys |
| Integers | `[-(2^53)+1, (2^53)-1]` — outside this range is rejected |
| Floats | **rejected**; binary floating point never appears in an exact monetary path |
| Strings | must already be NFC-normalized; non-NFC is rejected |
| Object keys | sorted by Unicode scalar value; duplicate keys rejected |
| Encoding | UTF-8, no BOM, no insignificant whitespace |

A digest is the lowercase string `sha256:` followed by 64 hex characters.

## Signature envelope

Signed payloads use a DSSE-style envelope (`veridian.dsse-envelope.v1`) over
pre-authentication encoding, with Ed25519. Payload types:

| Payload | Media type |
|---|---|
| Receipt statement | `application/vnd.veridian.receipt-statement.v1+json` |
| Execution permit | `application/vnd.veridian.execution-permit.v1+json` |
| Effect receipt | `application/vnd.veridian.effect-receipt.v1+json` |
| Witness statement | `application/vnd.veridian.witness-statement.v1+json` |

Signing over the pre-authentication encoding rather than the raw payload is what
stops a signature being replayed under a different payload type.

## Bundle contents

`veridian.proof-bundle.v1` carries exact bytes, base64-encoded for transport:

| Field | Schema ID | Purpose |
|---|---|---|
| `semantic_bytes` | `veridian.action-semantics.v1` or `.completion-semantics.v1` | What was proposed, in business terms |
| `authorization_envelope_bytes` | `veridian.authorization-envelope.v1` | Principal, audience, purpose, nonce, validity window, state and policy binding |
| `contract_bytes` | caller-defined | The verification contract; its digest appears in the decision |
| `snapshot_bytes` | `veridian.verification-snapshot.v1` | Exact evidence and verifier manifests evaluated |
| `transport_binding_bytes` | `veridian.transport-binding.v1` | How the proposal arrived — never affects authorization |
| `verifier_manifest_bytes[]` | `veridian.verifier-manifest.v1` | Identity of each verifier implementation |
| `evidence_ref_bytes[]` | `veridian.evidence-ref.v1` | Governed references; never embedded evidence or plaintext locators |
| `decision_bytes` | `veridian.decision.v1` | Clause results and the aggregate disposition |
| `receipt_envelope_bytes` | signed | Event metadata binding the decision |
| `witness_envelope_bytes[]` | signed, optional | Third-party attestations of the receipt chain |

## Verification algorithm

An independent verifier performs these steps in order. Any failure is a failure
of the whole bundle — there is no partial acceptance.

1. **Verify the receipt envelope** against a caller-supplied trusted key set.
   An unknown key id is `UNTRUSTED_SIGNER`, not a soft warning.
2. **Check the payload type** is the receipt statement type.
3. **Re-derive `sha256(decision_bytes)`** and require it to equal the receipt's
   `decision_digest`.
4. **Re-derive every disclosed binding** and require equality:
   - `sha256(contract_bytes)` == `decision.contract_digest`
   - `sha256(snapshot_bytes)` == `decision.snapshot_digest`
   - `sha256(authorization_envelope_bytes)` == `decision.authorization_envelope_digest`
     and == `snapshot.authorization_envelope_digest`
   - `sha256(semantic_bytes)` == `authorization.semantic_digest`
   - `sha256(transport_binding_bytes)` == `receipt.transport_binding_digest`
   - each `sha256(verifier_manifest_bytes[i])` appears in
     `decision.verifier_manifest_digests`
   - each `sha256(evidence_ref_bytes[i])` appears in
     `snapshot.evidence_ref_digests`
5. **Re-aggregate the disposition** from the clause results and require it to
   equal `decision.disposition`. A bundle claiming `ALLOW` over a violated hard
   clause is invalid regardless of its signature.
6. **Report replay and history status** — see below.

Steps 1–5 are decidable from the bundle alone. Step 6 is not.

## What a valid bundle does not prove

This is the part most easily overclaimed.

| Status | Meaning | To upgrade |
|---|---|---|
| `replay_status: not-checked` | No authoritative nonce registry or clock was supplied, so freshness is unknown | Supply a `ProofVerificationContext` with a `NonceRegistry` and verification time |
| `history_status: unanchored` | No independently retained head or witness was supplied, so a valid rollback, fork selection or suffix truncation is undetectable | Supply an `AnchorContext` with a head retained **outside** the system being audited |

`valid=True` means: *these bytes were signed by a key you trust, and every
disclosed binding is internally consistent.* It does not mean the decision is
current, that it was the only decision made, or that the evidence it cites was
true. A chain that vouches for itself vouches for nothing.

## Verifier implementation identity

Each `ClauseResultV1` carries a `verifier_manifest_digest`. The manifest
(`veridian.verifier-manifest.v1`) binds verifier id, semantic version,
`build_digest`, canonical configuration, input/output schema digests,
determinism, execution mode, required capabilities and resource limits.

For checks defined through `veridian.gate`, `build_digest` is derived over the
predicate's module, qualified name, declared version, canonical configuration,
**source digest** and runtime identity. Changing a predicate's body changes the
digest, so a decision cannot be replayed under a different implementation of the
same clause id.

Source is not always reachable — a predicate defined in a REPL or built by
`exec` has none. The manifest then records `source_bound: false` rather than
silently claiming a code binding it does not have. Treat `source_bound: false`
as unverifiable implementation identity and fail closed where policy requires
attested graders.

## Stability

The schema IDs and the algorithm above are frozen for the `v1` line. Additive
changes take a new schema ID; they do not redefine an existing one. A verifier
written against this document will keep accepting `v1` bundles.

Bundles are self-describing: every component carries its own `schema_id`, and a
verifier must reject a component whose `schema_id` it does not recognise rather
than attempt a best-effort parse.
