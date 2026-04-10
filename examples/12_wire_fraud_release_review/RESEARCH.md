# Research Notes: Wire Fraud Release Control

This note documents why this example focuses on high-value wire release and how
the implementation choices map to real operational risk.

## 1. Problem Selection

We selected wire-release control because it combines:

- high dollar impact
- social-engineering attack prevalence (BEC)
- low error tolerance once settlement occurs

## 2. External Evidence

## FBI IC3 (2024)

- IC3 reports **21,442 BEC complaints** in 2024 with adjusted losses over
  **$2.7 billion**.
- BEC commonly drives unauthorized fund transfers through compromised email
  flows.

Source:

- https://www.ic3.gov/Outreach/Brochures/IC3-Brochure.pdf
- https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf

## FinCEN Advisory (BEC)

- FinCEN outlines BEC typologies and institutional red flags involving
  unauthorized transfer requests, account changes, and spoofed communications.
- The advisory supports deterministic pre-release controls and documented
  escalation paths.

Source:

- https://www.fincen.gov/resources/statutes-regulations/guidance/advisory-financial-institutions-e-mail-compromise-fraud

## Federal Reserve / Fedwire

- Fedwire disclosures state payment finality is effectively immediate at
  settlement/advice of credit.
- This supports "prevent before release" architecture rather than
  post-settlement detection.

Source:

- https://www.frbservices.org/binaries/content/assets/crsocms/financial-services/wires/funds-service-disclosure.pdf

## FFIEC BSA/AML Guidance

- FFIEC references current SAR governance expectations, including treatment of
  continuing activity and documentation decisions.
- This supports explicit auditability and deterministic pause/review pathways.

Source:

- https://bsaaml.ffiec.gov/manual/AssessingComplianceWithBSARegulatoryRequirements/04_ep

## 3. Design Implications for the Example

The example implements:

1. Deterministic risk pre-screening to catch obvious high-risk patterns early.
2. Dual-approval pause gate for high-risk transfers.
3. Structured verification contract for consistent release decisions.
4. Replay-safe side-effect boundary for transfer execution (`run_activity`).
5. Explicit blocked path for sanctioned beneficiaries.

## 4. Success Criteria

- A sanctioned beneficiary case never reaches transfer execution.
- A high-risk case pauses until two approvals are persisted in task metadata.
- Replay of the release step does not duplicate gateway calls.
- Each completed task contains decision evidence and release state in ledger
  artifacts.
