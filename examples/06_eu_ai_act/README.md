# Problem 6: EU AI Act Compliance

## The Deadline

**August 2, 2026** — EU AI Act high-risk system requirements become fully enforceable.

| Violation | Penalty |
|-----------|---------|
| Prohibited AI practices | EUR 35M or 7% of global turnover |
| Record-keeping failures | EUR 15M or 3% of global turnover |
| Market withdrawal | Authorities can ORDER systems removed |

Finland became the first EU member state with full enforcement powers in December 2025. This is not theoretical.

Sources: [EU AI Act Article 12](https://artificialintelligenceact.eu/article/12/), [Article 99 Penalties](https://artificialintelligenceact.eu/article/99/), [LegalNodes](https://www.legalnodes.com/article/eu-ai-act-2026-updates-compliance-requirements-and-business-risks)

## What Article 12 Requires

High-risk AI systems must automatically log:
- Events relevant for risk identification
- Period of each use (start/end timestamps)
- Input data for which decisions were made
- Model version used for each decision
- Verification evidence
- Active policies and safety measures
- Logs retained at least 6 months
- Tamper-evident (retroactive modification must be detectable)

## Root Cause

Most AI agent frameworks produce no audit trail whatsoever. The agent runs, produces output, and moves on. No record of which model generated it, what verifier checked it, or what policy was active. When a regulator asks "prove this output was verified" — there is nothing to show.

## Veridian's Solution

```
ProofChain (SHA-256 hash-linked):
  Entry 1: task_spec_hash | verifier_config | model_version | output_hash | policy | HMAC
      ↓ previous_hash
  Entry 2: task_spec_hash | verifier_config | model_version | output_hash | policy | HMAC
      ↓ previous_hash
  Entry 3: ...

Tamper attempt → hash chain breaks → mathematically detectable
```

Every entry is linked to the previous via SHA-256. Optional HMAC signing proves authenticity. Changing ANY entry after the fact breaks the chain — a compliance officer can mathematically verify that logs were not altered post-hoc.

`ComplianceReportGenerator` produces human-readable reports for EU AI Act, NIST AI RMF, and OWASP Agentic Top 10.

## Run

```bash
cd examples/06_eu_ai_act
python solution.py        # Full demo with tamper detection
pytest test_solution.py -v  # Tests including save/load integrity
```

## What This Proves

- Every decision traceable to model version + verifier + policy
- Retroactive tampering is mathematically detectable
- Reports are machine-parseable (JSON) and human-readable (markdown)
- Chain integrity survives save/load cycle
- Article 12 requirements satisfied: logging, traceability, retention, tamper evidence
