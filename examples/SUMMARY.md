# Veridian Examples — 8 Real-World Problems Solved

Every problem below is documented with real incidents from 2025-2026. Each solution is a production-ready, runnable Veridian implementation.

## Solution Matrix

| # | Problem | Real Incident | Veridian Primitive | Tests | Status |
|---|---------|--------------|-------------------|-------|--------|
| 1 | AI Code Deploy | Amazon 6hr outage, 6.3M orders lost | `CodeDeployVerifier` (AST) | 10 | Shipped |
| 2 | Runaway Costs | $60K overnight cloud bill | `CostCeilingVerifier` | 7 | Shipped |
| 3 | Hallucinated Evidence | Mata v. Avianca, lawyer sanctioned | `CitationVerifier` | 8 | Shipped |
| 4 | Healthcare Misdiagnosis | 66% misdiagnosis rate, #1 safety threat | `DiagnosisConsistencyVerifier` (N=5) | 9 | Shipped |
| 5 | Financial Cascade | Single misclassification → regulatory | `TransactionClassificationVerifier` | 9 | Shipped |
| 6 | EU AI Act | EUR 35M fines, Aug 2 2026 deadline | `ProofChain` + `ComplianceReport` | 8 | Shipped |
| 7 | Pilot Failure | MIT: 95% zero ROI | `DriftDetector` + `BehavioralFingerprint` | 8 | Shipped |
| 8 | Deleted Databases | 10+ incidents, 15 years data lost | `DestructiveCommandVerifier` | 16 | Shipped |

## The Point

The verification-to-application code ratio is the metric that matters. A high ratio means the safety guarantees are explicit and inspectable. A low ratio means you are trusting the model.

**The LLM reasons. Python verifies.**

## Run All

```bash
cd examples/
for d in 01_* 02_* 03_* 04_* 05_* 06_* 07_* 08_*; do
  echo "=== $d ==="
  cd "$d" && python solution.py && cd ..
done
```
