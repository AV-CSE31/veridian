# Problem 11: SOC Incident Containment Copilot

## Positioning Goal

Show that Veridian can run security-critical response workflows with verified evidence, policy controls, and mandatory human gates before containment actions.

## What This Example Should Prove

- alerts are triaged with evidence-backed reasoning
- dangerous containment actions require explicit approval
- containment commands are replay-safe and not duplicated
- the full incident decision chain is auditable

## Target Workflow

```text
ingest alert
  -> enrich with telemetry and threat intel
  -> verify evidence quality and source grounding
  -> classify severity and recommend action
  -> if action is disruptive (isolate host, revoke creds), PAUSE for SOC lead
  -> execute containment activity after approval
  -> validate post-action system health
```

## Veridian Components To Highlight

- verification pipeline for evidence quality and grounding
- policy-driven `allow/warn/block/retry_with_repair` behavior
- pause/resume lifecycle with persistent payload
- activity journal for idempotent containment commands
- trace and replay artifacts for after-action review

## Demo Inputs

- `low_severity_alert.json` (expected auto-allow path)
- `high_severity_alert.json` (expected PAUSE + approval path)
- `fabricated_ioc_alert.json` (expected BLOCK path)

## Acceptance Criteria

- fabricated or weak evidence cannot trigger containment execution
- approval-required path resumes correctly after restart
- repeated runner invocation does not duplicate containment command
- replay output provides complete timeline for incident postmortem

## Test Plan

- unit: evidence verifier and policy action mapping
- integration: high-severity pause/resume with approval payload
- integration: idempotent activity calls for containment action
- regression: no containment on blocked verification outcomes

## Why This Is Strategic

Security teams care about false positives, action safety, and audit trails. This example showcases Veridian as correctness infrastructure for high-risk operations.

