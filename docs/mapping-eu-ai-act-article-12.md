# Mapping: EU AI Act Article 12 record-keeping

**This document does not claim that using Veridian makes a system compliant with
the EU AI Act.** Compliance is a property of an organisation's whole programme —
scope determination, risk management, retention, monitoring, human oversight,
documentation and governance. Veridian supplies evidence primitives that can
*support* specific record-keeping obligations. It supplies nothing else, and it
is not legal advice.

## Scope first

Article 12 binds **high-risk** AI systems. Annex III obligations became
applicable on 2 August 2026. Most agent deployments are not high-risk and are
therefore not in scope for Article 12 at all. Determining whether a system is in
scope is the deployer's responsibility and is not something a library can
answer.

If your system is out of scope, the artifacts below are still useful as ordinary
audit evidence. They are simply not Article 12 artifacts.

## What the obligation asks for

Article 12 requires high-risk systems to technically allow automatic recording
of events over their lifetime, such that logs enable:

- identification of situations that may present a risk or a substantial
  modification;
- post-market monitoring;
- monitoring of operation.

Records must enable post-hoc reconstruction of individual AI-assisted decisions,
and be retained so that traceability is preserved. Regulators read "appropriate
to the intended purpose" as implying tamper-evidence.

## Where Veridian artifacts line up

| Obligation characteristic | Veridian artifact | What it actually gives you |
|---|---|---|
| Reconstruct an individual decision | `DecisionPayloadV1` | Every clause, its status, reason code, evidence references, and the aggregate disposition — not a summary |
| Identify what the decision was *about* | `ActionSemanticsV1` | Business meaning, digest-bound, separate from transport |
| Identify who and under what authority | `AuthorizationEnvelope` | Principal, delegation chain, audience, purpose, validity window |
| Identify the inputs evaluated | `VerificationSnapshotV1` + `EvidenceRef` | Exact evidence references with producer, observation time, trust class — references, never embedded payloads |
| Identify the deciding implementation | `VerifierManifestV1` | Verifier id, version, config, build digest, runtime |
| Tamper-evidence | Ed25519 over Canonical JSON Profile v1 | Any byte change invalidates the signature |
| Sequencing and continuity | `ReceiptStatementV1` | Hash-chained `previous_receipt_digest`, monotonic sequence, stream id |
| Independent checkability | `verify_proof_bundle` | A third party with public keys can re-derive every binding without contacting you |
| What was actually executed | `EffectReceiptV1` | Binds permit, outbox, result and external reference |

## Where Veridian gives you nothing

State these plainly to anyone who asks whether this "covers Article 12":

- **Retention.** Veridian writes artifacts. It does not retain them, enforce a
  retention period, or manage lifecycle. That is your storage and policy.
- **Scope determination.** Nothing here tells you whether your system is
  high-risk.
- **Risk management, oversight, documentation.** Articles 9, 11, 13, 14 and 17
  are untouched.
- **Log completeness.** Veridian records the decisions it is asked to make. An
  action taken around the gate is not recorded — because it never reached it.
- **Durable receipt chaining out of the box.** `Gate` maintains its receipt
  sequence **in process memory**. Across restarts the chain restarts. Durable
  chaining requires supplying your own journal; do not represent an in-process
  chain as an append-only record.
- **Anchoring.** Without an independently retained head, `history_status` is
  `unanchored` and a valid rollback is undetectable. An unanchored local chain
  is not tamper-*evident* against an operator who controls the storage — only
  against an outsider.
- **The legacy HMAC paths.** `verify_completion` and the JSONL report chain use
  symmetric keys. Any key holder can rewrite a valid chain. Do not use them as
  regulatory evidence; use the Ed25519 assurance kernel.

## An honest summary sentence

If you need one line for a compliance conversation:

> Veridian produces signed, tamper-evident, independently verifiable records of
> individual agent authorization decisions and their executed effects, which can
> serve as source records supporting Article 12 record-keeping obligations. It
> does not implement retention, scope determination, or any other Article 12
> requirement, and it is not a compliance product.

## Related

- [proof-format.md](proof-format.md) — exactly what a verifier can and cannot
  establish from a bundle.
- [threat-model.md](threat-model.md) — what defeats the chain.
