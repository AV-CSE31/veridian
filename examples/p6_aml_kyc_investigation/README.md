# P6 — AML/KYC Investigation Automation

## The Problem

Mid-size banks process **10,000–50,000 AML alerts per month**. More than **95% are false
positives**, yet each requires manual review to satisfy regulatory obligations. Investigators
spend 40–60% of their time on mechanical case assembly — pulling transaction records,
cross-referencing customer profiles, formatting investigation reports.

Under **GDPR Art. 22** (automated decision accountability) and **SOX** (audit trail
requirements), every automated decision must be traceable to specific evidence, explainable
to a regulator, and include a human override path for high-risk cases.

## The Veridian Solution

This example demonstrates a **10-alert AML investigation pipeline** with full provenance,
semantic consistency enforcement, and automatic escalation.

### What Veridian provides

| Component | What it does |
|-----------|-------------|
| `SchemaVerifier` | Enforces every report has `risk_level`, `evidence_summary`, `recommended_action`, `source_documents` |
| `SemanticGroundingVerifier` | Catches conclusions that contradict evidence (e.g. `risk_level=LOW` but `recommended_action=FREEZE_ACCOUNT`) |
| `CrossRunConsistencyHook` | Detects when the same customer receives contradictory risk ratings across separate alert investigations |
| `AuditLogHook` (custom) | SHA-256 evidence hash for every decision — GDPR Art. 22 / SOX compliance |
| `EscalationTracker` (custom) | Surfaces HIGH/CRITICAL cases to the Human Review Queue |

### Pipeline flow

```
AML alert intake
    → TaskLedger (10 investigation tasks)
    → VeridianRunner
        → WorkerAgent (MockProvider simulates investigator LLM)
        → CompositeVerifier (schema check → semantic grounding check)
        → AuditLogHook (evidence hash)
        → EscalationTracker (HIGH/CRITICAL flagging)
        → ConsistencyBridgeHook → CrossRunConsistencyHook
    → Rich summary: alerts processed, false positives, escalations, contradictions
```

### Demonstrated scenarios

- **AML-001, 009, 010**: LOW risk → `CLOSE_ALERT` (false positives, auto-closed)
- **AML-002, 007**: MEDIUM risk → `MONITOR` / `ADDITIONAL_SCREENING`
- **AML-003, 005, 008**: HIGH risk → `ESCALATE` (added to Human Review Queue)
- **AML-004**: CRITICAL risk → `FREEZE_ACCOUNT` (emergency escalation)
- **AML-005 vs AML-001** (both CUST-A): **CRITICAL conflict** — same customer rated LOW then HIGH
- **AML-006 vs AML-002** (both CUST-B): **warning conflict** — MEDIUM → LOW inconsistency

## How to run

```bash
cd /path/to/veridian
python examples/p6_aml_kyc_investigation/p6_aml_kyc.py
```

No API keys required — the example uses `MockProvider` with scripted responses.

## Files

| File | Description |
|------|-------------|
| `p6_aml_kyc.py` | Main example script |
| `sample_alerts.json` | 10 synthetic AML alerts |

## Compliance alignment

| Requirement | How Veridian satisfies it |
|-------------|--------------------------|
| GDPR Art. 22 — traceability | SHA-256 hash of every decision's evidence fields |
| SOX — audit trail | Immutable ledger with timestamped entries per task |
| FinCEN/BSA — SAR escalation | EscalationTracker auto-queues HIGH/CRITICAL cases |
| GDPR — human override | CrossRunConsistencyHook flags contradictions; human arbitration required |
