# Mastra Integration

Use Mastra for TypeScript agent applications, workflows, memory, tools, and
observability. Use Veridian as a language-neutral verification boundary for
production steps that need deterministic completion evidence.

## Support Status

Support level: universal sidecar pattern.

There is no first-class Mastra adapter in this Python repository yet. The
production path is to call Veridian from a Python worker, CLI/API boundary, or
service sidecar.

## Production Architecture

```text
Mastra workflow step
  -> emits structured output
  -> calls Veridian sidecar/worker
  -> Veridian runs deterministic verifier
  -> Mastra continues, retries, suspends, or escalates based on pass/fail
```

Mastra owns the TypeScript agent app. Veridian owns the verification ledger and
evidence contract.

## Minimal Boundary Shape

Use a stable JSON envelope between Mastra and Veridian:

```json
{
  "task_id": "payment-review-123",
  "verifier_id": "schema",
  "verifier_config": {
    "required_fields": ["decision", "reason"]
  },
  "output": {
    "decision": "hold",
    "reason": "beneficiary requires manual review"
  }
}
```

A Python worker can turn that envelope into `UniversalVerifier.check()` or a
full `TaskLedger` task.

## Production Hardening

- Make the Python verification worker stateless except for ledger storage.
- Require idempotency keys for calls from Mastra workflow steps.
- Return a machine-readable pass/fail/error envelope to TypeScript callers.
- Persist framework run IDs, step IDs, and workflow IDs in Veridian metadata.
- Use Veridian verifier IDs and configs from version-controlled deployment
  files, not dynamic agent-generated values.
- Add circuit breakers around the sidecar call so Mastra can route to review
  instead of silently accepting unverified output.

## Adapter Candidate Scope

A future TypeScript package should expose a `verifyStep()` helper, a Mastra
workflow middleware, and a typed result envelope compatible with Veridian replay
reports.

