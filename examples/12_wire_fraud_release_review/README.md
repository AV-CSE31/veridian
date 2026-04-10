# Problem 12: Wire Fraud Release Review

Mission-critical release control for high-value wire transfers in Business Email
Compromise (BEC) conditions.

## Why This Problem (Research Narrowing)

- FBI IC3 reports BEC remains one of the highest-loss cyber-enabled crimes. In
  2024 it logged **21,442 BEC complaints** with adjusted losses above
  **$2.7B**.  
  Source: [IC3 Brochure (2024)](https://www.ic3.gov/Outreach/Brochures/IC3-Brochure.pdf),
  [IC3 Annual Report (2024)](https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf)
- FinCEN warns BEC schemes push unauthorized transfers via social engineering
  and compromised business email workflows.  
  Source: [FinCEN BEC Advisory](https://www.fincen.gov/resources/statutes-regulations/guidance/advisory-financial-institutions-e-mail-compromise-fraud)
- Fedwire settlement finality is immediate: payment is final and irrevocable at
  credit/advice. This makes pre-release controls the critical control point.  
  Source: [Fedwire PFMI Disclosure](https://www.frbservices.org/binaries/content/assets/crsocms/financial-services/wires/funds-service-disclosure.pdf)
- FFIEC BSA/AML guidance emphasizes SAR governance and decision documentation
  for suspicious activity controls.  
  Source: [FFIEC BSA/AML Manual](https://bsaaml.ffiec.gov/manual/AssessingComplianceWithBSARegulatoryRequirements/04_ep)

## What This Example Implements

1. Deterministic risk pre-screening for each payment before model execution.
2. Dual-approval pause gate for high-risk wires (`TaskPauseRequested`).
3. Composite verification contract for wire decision payloads.
4. Replay-safe transfer release with `run_activity()` and persisted activity journal.
5. Explicit blocked path for sanctioned beneficiaries (never released).

## Workflow

```text
payment intake
  -> deterministic risk score + sanction signal
  -> if risk high, PAUSE until two approvers are present
  -> worker generates structured release decision
  -> composite verifier enforces decision consistency
  -> release stage executes only ALLOW decisions via run_activity()
  -> replay pass returns cached release receipt (no duplicate transfer)
```

## Demo Inputs

- `normal_payment.json` -> expected `ALLOW` and release
- `suspicious_payment.json` -> expected `PAUSED` then `ALLOW` after dual approval
- `sanctioned_beneficiary.json` -> expected `BLOCK` and no release activity

## Run

```bash
python examples/12_wire_fraud_release_review/pipeline.py
```

## Test

```bash
pytest examples/12_wire_fraud_release_review/test_pipeline.py -q
```

## Mission-Critical Acceptance Checks

- blocked payment never calls the transfer gateway
- dual-approval pause survives restart and resumes deterministically
- replaying release flow does not duplicate external transfer calls

## Truthful Claims and Boundaries

Use these statements in external communication to avoid over-claiming.

What we can claim now:

- This is a production-pattern reference for wire-release correctness controls.
- It demonstrates deterministic HITL pause/resume and replay-safe side-effect execution.
- It proves key failure modes with executable tests (block, pause/resume, idempotent release).

What we should not claim yet:

- This is not a live banking rails integration.
- This is not full sanctions screening infrastructure.
- This is not a complete compliance platform with regulator-ready reporting.

Current example-grade assumptions:

- Uses `MockProvider` for deterministic behavior.
- Uses a stub `WireGateway` (no external settlement network).
- Uses scenario inputs for sanctions and approval signals.
