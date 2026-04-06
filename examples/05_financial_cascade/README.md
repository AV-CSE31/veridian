# Problem 5: Financial Transaction Cascade

## The Problem

A single AI hallucination misclassifying a financial transaction cascades across linked systems — triggering compliance violations, financial misstatements, and regulatory action.

**Industry data:**
- AML regulatory fines increased **417%** in H1 2025 vs H1 2024 (~$1.23 billion total)
- If most institutions rely on models from the same AI vendor, a single failure triggers **systemic cascading effects**
- California AB 316 (Jan 1, 2026): AI's autonomous operation is **NOT a defense** to liability claims
- Upcoming EU/US frameworks demand **explainability** for AI-driven AML decisions

Sources: [ComplyAdvantage](https://complyadvantage.com/insights/the-biggest-aml-fines-in-2025/), [Feedzai](https://www.feedzai.com/blog/future-aml-compliance-predictions/)

## Root Cause

```
AI classifies transaction as LOW risk
  -> Action: CLEAR (transaction passes)
  -> But the transaction actually matches sanctions list
  -> Downstream systems: cleared by AI, no further review
  -> Regulatory audit: "Why was a sanctioned entity cleared?"
  -> Fine: up to 7% of global turnover
```

The root failure: **cross-field inconsistency.** The risk level and the action contradict each other. A deterministic consistency check catches this instantly.

## Veridian's Fix

`AMLClassificationVerifier` — enforces a **risk-action consistency matrix**:

| Risk Level | Allowed Actions |
|------------|----------------|
| LOW | CLEAR, FLAG |
| MEDIUM | FLAG, ESCALATE |
| HIGH | ESCALATE, BLOCK |
| CRITICAL | BLOCK only |

Additionally checks:
- All required fields present (risk_level, action, justification, regulation_cited)
- Valid enumeration values (no "MAYBE" or "UNKNOWN" risk levels)

This is the same pattern as Veridian's `SemanticGroundingVerifier` — cross-field consistency checking. Deterministic. No LLM call.

## Run

```bash
cd examples/05_financial_cascade
python solution.py
pytest test_solution.py -v
```

## What This Proves

A CRITICAL risk transaction paired with a CLEAR action is caught instantly — before it enters the compliance pipeline. The 417% increase in AML fines represents institutions where this cross-field check didn't exist.
