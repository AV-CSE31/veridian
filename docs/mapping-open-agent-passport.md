# Mapping: Open Agent Passport and AP2

How Veridian's objects line up with two external vocabularies for agent
authorization, and what would be involved in speaking them on the wire.

**Status: analysis, not implementation.** Veridian does not currently emit or
consume OAP or AP2 objects. This document exists so the correspondence is
written down before anyone needs it, and so the cost of interoperating is known
rather than guessed. The correspondence below is drawn from the published
summaries of each specification; it has not been validated clause-by-clause
against the normative text, and should be treated as a starting point for that
work rather than its conclusion.

## Open Agent Passport (OAP) v1.0

OAP v1.0 (published March 2026) decomposes agent authorization into three
components: the **Passport** (identity plus capabilities), the **Decision**
(authorization outcome), and the **Proof** (audit trail).

Veridian arrived at the same decomposition independently:

| OAP component | Veridian analogue | Correspondence |
|---|---|---|
| Passport — identity + capabilities | `AuthorizationEnvelope` (`principal_id`, `delegation_chain`, `audience`, `purpose`) | Close. Veridian binds the authorization to one action digest and validity window; OAP treats the passport as a longer-lived credential. Veridian has no standing capability catalogue. |
| Decision — authorization outcome | `DecisionPayloadV1` (`Disposition`, `ClauseResultV1[]`) | Close. Veridian's three-valued `ALLOW`/`DENY`/`HOLD` distinguishes "refused" from "could not decide"; a two-valued decision loses that. |
| Proof — audit trail | `ProofBundleV1` + `ReceiptStatementV1` | Close. Veridian additionally binds verifier implementation identity per clause. |
| — | `ExecutionPermitV1` | No direct OAP analogue. The single-use permit binding an exact action digest is Veridian-specific. |
| — | `EffectReceiptV1` | No direct OAP analogue. Attestation of what was *executed*, not what was authorized. |

The gap in both directions is instructive: OAP has a richer standing-identity
model; Veridian has a richer execution-and-evidence model.

## AP2 (Agent Payments Protocol)

AP2 carries Intent, Cart and Payment **Mandates** as W3C Verifiable Credentials.
Each mandate names an issuer, a subject, a payload and a signature, and any
party can verify the chain without contacting the issuer.

| AP2 concept | Veridian analogue | Correspondence |
|---|---|---|
| Mandate as signed, independently checkable claim | Signed permit / receipt over Canonical JSON Profile v1 | Same property, different envelope. AP2 uses W3C VC; Veridian uses a DSSE-style envelope. |
| Intent Mandate — scope the user authorized | `AuthorizationEnvelope` (`purpose`, validity window) | Partial. Veridian binds one action, not a standing scope. |
| Cart Mandate — the exact transaction | `ActionSemanticsV1` + `semantic_digest` | Strong. Both make the exact transaction the thing that is signed over. |
| Payment Mandate — the executed payment | `EffectReceiptV1` | Strong, including the external reference digest. |
| Offline verifiability | `verify_proof_bundle` | Same property. |

## What interoperating would cost

The internal model would not need to change. Serialization is additive:

1. **A VC/JSON-LD serializer** emitting Veridian objects in W3C Verifiable
   Credential shape, alongside the native encoding. The hard part is not the
   mapping — it is that JSON-LD canonicalization differs from Canonical JSON
   Profile v1, so the *digest* over a VC-shaped object is not the digest over
   the native object. Both would need to be disclosed and bound.
2. **An OAP profile document** stating exactly which Veridian fields populate
   which OAP fields, and which OAP fields Veridian cannot populate (standing
   capability sets, in particular).
3. **Conformance vectors** — fixed inputs with expected bytes, so a third party
   can check an implementation rather than trust it.

Estimated effort is small relative to the assurance kernel itself. The reason it
is not scheduled is not cost, it is demand: no integrator has asked, and
building a wire format for a hypothetical consumer is how the earlier platform
era of this project went wrong.

## When to revisit

Implement a serializer when **any** of these becomes true:

- a named integrator requires OAP or AP2 on the wire;
- an OAP reference implementation exists in Python that can be adopted rather
  than reimplemented;
- AP2 adoption reaches the point where a payments counterparty rejects
  non-VC-shaped evidence.

Until then this document is the deliverable, and it is deliberately cheaper than
the code it describes.

## Sources

The specification summaries this mapping is drawn from are cited in the
repository's audit notes rather than reproduced here; both specifications are
public and should be read normatively before any implementation work begins.
