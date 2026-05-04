# Production Integration Checklist

Use this checklist before calling any integration production-supported.

## Runtime Contract

- The verified framework step is named and stable.
- The verifier ID and config are version-controlled.
- The failure behavior is explicit: raise, retry, pause, DLQ, or human review.
- The verifier error is visible to operators and retry prompts.
- The model/provider version and framework run ID are recorded in metadata.

## Evidence And Replay

- Ledger storage is durable for the deployment tier.
- Checkpoints are persisted after framework steps that may need replay.
- `strict_replay` is enabled for drift-sensitive workflows.
- Replay reports are included in incident triage.
- Verification evidence is retained for the required audit window.

## Version Governance

- Framework and Veridian versions are pinned.
- Certified adapters run their certification tests in CI.
- Universal integrations have at least one application-level smoke test.
- Upgrade PRs include failure-mode notes and rollback instructions.

## Security Boundaries

- Veridian verifiers are not written by the agent being verified.
- Tool execution is sandboxed by the host application when executing untrusted
  code. Veridian's `TrustedExecutor` is a command wrapper and output sanitizer,
  not a complete sandbox boundary.
- Secrets and PII are filtered before traces leave the trusted environment.
- Human approval is used for irreversible or regulated actions.

## Operational Readiness

- Operators know where to inspect verifier failures.
- DLQ or review queues exist for repeated verifier failures.
- Alerts distinguish framework execution failures from verifier failures.
- Runbooks cover replay, retry, and policy override.

