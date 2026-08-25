# Security Policy

## Reporting a Vulnerability

Please use GitHub's private vulnerability-reporting flow for this repository:

<https://github.com/AV-CSE31/veridian/security/advisories/new>

Do not include exploit details, credentials, personal data or production proof
bundles in a public issue. If private reporting is unavailable, open a public
issue containing only a request for a private contact channel.

Useful reports identify the affected commit/version, trust assumptions, minimal
reproduction, impact and whether keys, credentials or authoritative evidence
producers were compromised. Test only systems and data you are authorized to
use.

## Supported Code

Veridian is alpha software. Security fixes target the current default branch and
the latest published release when the issue is reproducible there. Older source
snapshots may require upgrading. A version number in a checkout does not imply
that the same artifact has been published to PyPI.

## Security Model

- Agents propose actions; credential-holding executors must remain outside the
  agent trust boundary.
- Production signing keys must be supplied explicitly through an operator-owned
  signer, KMS or HSM. Veridian intentionally ships no fallback secret.
- Offline signature verification alone does not establish freshness, single-use
  status or append-only history. Those claims require authoritative time,
  state/nonce stores and independently retained heads or witnesses.
- The legacy completion/report JSONL paths use HMAC. Any holder of that symmetric
  key can forge or rewrite a valid chain; prefer Ed25519 proof bundles for
  independently checkable receipts.
- `IsolatedVerifierRunner` is a bounded subprocess protocol, not an OS security
  sandbox.
- The reference WAL ledger and SQLite permit/outbox store are single-host
  components, not distributed consensus systems.
- Exactly-once economic effects require the downstream adapter to honor the
  supplied idempotency key.

See the README's **Current Boundaries** section for additional non-guarantees.

## Handling Secrets and Evidence

Do not commit private keys, signing secrets, credentials, raw banking data or
confidential proof evidence. Completion/report payloads and diagnostics are
redacted by default, but application-supplied evidence, telemetry exporters and
domain adapters still require an operator-defined minimization and retention
policy.
