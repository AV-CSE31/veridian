# Evidence Timeline

The evidence timeline is the operator view of why a task was or was not allowed
to complete.

Veridian already has runtime pieces that support this story:

- ledger state transitions
- replay snapshots and reports
- `VeridianTracer` JSONL/OTel events
- `ProofChain` hash-linked proof entries
- `RunTimeline` operator formatting

v0.4 should document and test the minimum production timeline before adding new
storage surfaces.

## Minimum Timeline Events

Every production run should be able to answer:

- when the run started
- when the task was claimed
- what framework step produced the output
- which verifier ran
- whether verification passed or failed
- which error blocked completion
- when the task transitioned to `DONE`, `FAILED`, `PAUSED`, or `ABANDONED`
- where replay evidence can be inspected

## Evidence Fields

Recommended fields for every verifier event:

- `run_id`
- `task_id`
- `framework`
- `framework_run_id`
- `verifier_id`
- `verifier_config_hash`
- `input_hash`
- `output_hash`
- `passed`
- `error`
- `duration_ms`
- `policy_version`
- `veridian_version`

## Retention

Production deployments should retain evidence long enough to cover incident
review, regulatory review, and customer-support windows. Secrets and PII should
be filtered before evidence leaves the trusted environment.

## v0.5 Target

v0.5 should expose a first-class evidence export format that can be consumed by
external eval and review systems. Inspect AI should be the first target because
its sandbox, eval, and approval story is credible with serious engineering
teams.
