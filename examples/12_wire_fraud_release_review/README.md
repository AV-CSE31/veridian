# Problem 12: Wire Fraud Release Review

## Positioning Goal

Show that Veridian can enforce correctness and policy in financial approval flows where mistakes have immediate monetary impact.

## What This Example Should Prove

- high-value payment instructions are validated before release
- risky payments are paused for dual approval
- final release is idempotent across crashes/retries
- every decision has an audit-friendly verification trail

## Target Workflow

```text
receive payment request
  -> verify beneficiary identity and account consistency
  -> verify policy thresholds and sanction signals
  -> score anomaly and fraud indicators
  -> if above risk threshold, PAUSE for dual approval
  -> on approval, execute transfer activity
  -> verify settlement confirmation and close task
```

## Veridian Components To Highlight

- composite verifier contract for identity + policy + anomaly checks
- PRM policy actioning (`allow/warn/block/retry_with_repair`)
- pause/resume with reasoned approval payload
- `run_activity()` for exactly-once style payment release behavior
- replay and audit outputs for compliance review

## Demo Inputs

- `normal_payment.json` (expected PASS)
- `suspicious_payment.json` (expected PAUSE + dual approval)
- `sanctioned_beneficiary.json` (expected BLOCK)

## Acceptance Criteria

- blocked payment never calls transfer activity
- dual-approval flow survives restart with preserved pause context
- resumed run does not issue duplicate transfer call
- replay/diff commands clearly show policy and verification reasons

## Test Plan

- unit: payment verifier matrix and risk threshold behavior
- integration: suspicious payment pause -> resume -> settlement complete
- integration: duplicate-call prevention on resumed execution
- regression: sanction hit always blocks release

## Why This Is Strategic

Payments are an executive-level risk surface. Proving deterministic, policy-backed release control positions Veridian as enterprise-grade correctness infrastructure.

