# Problem 10: Production Deploy Gatekeeper

## Positioning Goal

Show that Veridian is not only an agent orchestrator. It is a deployment safety system with deterministic checks, resumable approvals, and replay-safe execution.

## What This Example Should Prove

- unsafe code or migration plans are blocked before deployment
- human approval pauses survive process restarts
- retries do not duplicate external side effects
- every deploy decision is replayable and auditable

## Target Workflow

```text
plan release
  -> run static/code safety verifiers
  -> run migration safety verifiers
  -> run blast-radius and rollback checks
  -> if high risk, PAUSE for approver
  -> on approval, execute deployment activity
  -> verify post-deploy health and finalize
```

## Veridian Components To Highlight

- `TaskLedger` for durable state transitions
- `TaskPauseRequested` / `PAUSED` + resume flow for human approval
- `run_activity()` journal to prevent duplicate deploy calls
- replay compatibility checks for strict drift protection
- DLQ path for abandoned or unsafe rollout attempts

## Demo Inputs

- `safe_release.yaml` (expected PASS)
- `unsafe_release.yaml` (expected BLOCK)
- `high_risk_release.yaml` (expected PAUSE -> RESUME -> PASS)

## Acceptance Criteria

- BLOCK case never calls deploy activity
- PAUSE case resumes with same checkpoint and no repeated side effects
- strict replay rejects run when model/verifier config drift is injected
- replay CLI shows clear snapshot and activity history for each run

## Test Plan

- unit: verifier contract checks and policy routing
- integration: pause -> crash -> resume -> deploy completion
- integration: resumed run does not re-call deploy activity
- regression: DLQ receives abandoned deploy attempts with triage metadata

## Why This Is Strategic

This is a high-trust enterprise scenario. If Veridian can guarantee deploy correctness and auditability, it is positioned as production reliability infrastructure, not a wrapper.

