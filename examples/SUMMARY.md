# Veridian Examples: High-Impact Showcase

This index has been curated to show only the highest-impact examples first.
Selection criteria:

- high business or safety blast radius
- clear differentiation for Veridian (verification, durability, replay, policy)
- implemented today (not just a spec)
- usable in demos and release narratives

## Flagship Examples (Show These First)

| Priority | Example | Why it is high impact | Veridian differentiator | Maturity |
|---|---|---|---|---|
| 1 | `08_deleted_databases/` | Prevents catastrophic data loss and production destruction | AST safety + threat classification + security reporting | Implemented + tests |
| 2 | `03_hallucinated_evidence/` | Prevents legal/credibility failure from fabricated citations | Multi-layer citation verification pipeline | Implemented + tests |
| 3 | `06_eu_ai_act/` | Direct regulatory and audit-readiness consequences | Proof chain + compliance evidence | Implemented + tests |
| 4 | `p9_crash_recovery/` | Core runtime reliability for long-running workflows | Atomic ledger + crash resume semantics | Implemented demo |
| 5 | `09_prm_policy_repair/` | Shows policy-driven repair/block as correctness infrastructure | PRM policy actions in runtime loop | Implemented demo |
| 6 | `p6_aml_kyc_investigation/` | Financial compliance and investigator throughput | Composite verification + consistency + escalation | Implemented demo |

## Secondary Examples (Keep, But Do Not Lead With)

| Example | Reason not in flagship set |
|---|---|
| `01_ai_code_deploy/` | Useful, but narrower than database deletion and deploy gatekeeper narrative |
| `02_runaway_costs/` | Important FinOps example, but comparatively simple |
| `04_healthcare_misdiagnosis/` | Strong domain story, but currently a smaller technical slice |
| `05_financial_cascade/` | Good consistency check example, less architectural breadth |
| `07_pilot_failure/` | Valuable drift story, less immediate than safety/compliance incidents |
| `drift-detection/` | Focused technical demo for one hook, not a full workflow |
| `skill-optimization/` | Research suite, strong internally, less direct for external buyer demos |

## Spec-Only Blueprints (High Potential, Not Yet Implemented)

These are excellent next flagship candidates once runnable code and tests land:

- `10_deploy_gatekeeper/`
- `11_soc_incident_containment/`
- `12_wire_fraud_release_review/`

## Audit Matrix (All Example Folders)

| Example | README | Runnable entrypoint | Tests | Showcase status |
|---|---|---|---|---|
| `01_ai_code_deploy` | Yes | `solution.py` | Yes | Secondary |
| `02_runaway_costs` | Yes | `solution.py` | Yes | Secondary |
| `03_hallucinated_evidence` | Yes | `pipeline.py` | Yes | Flagship |
| `04_healthcare_misdiagnosis` | Yes | `solution.py` | Yes | Secondary |
| `05_financial_cascade` | Yes | `solution.py` | Yes | Secondary |
| `06_eu_ai_act` | Yes | `solution.py` | Yes | Flagship |
| `07_pilot_failure` | Yes | `solution.py` | Yes | Secondary |
| `08_deleted_databases` | Yes | `pipeline.py` | Yes | Flagship |
| `09_prm_policy_repair` | Yes | `demo.py` | No | Flagship |
| `10_deploy_gatekeeper` | Yes | No | No | Spec-only |
| `11_soc_incident_containment` | Yes | No | No | Spec-only |
| `12_wire_fraud_release_review` | Yes | No | No | Spec-only |
| `drift-detection` | Yes | `run_drift_demo.py` | No | Secondary |
| `p6_aml_kyc_investigation` | Yes | `p6_aml_kyc.py` | No | Flagship |
| `p9_crash_recovery` | Yes | `p9_crash_recovery.py` | No | Flagship |
| `skill-optimization` | No (`EXPERIMENTS.md`) | `run_all_experiments.py` | No | Secondary |

## Recommended Demo Order

1. `08_deleted_databases`
2. `03_hallucinated_evidence`
3. `06_eu_ai_act`
4. `p9_crash_recovery`
5. `09_prm_policy_repair`
6. `p6_aml_kyc_investigation`

## Fast Validation Commands

```bash
# Existing example tests (single run from repo root)
pytest examples/01_ai_code_deploy/test_solution.py \
       examples/02_runaway_costs/test_solution.py \
       examples/03_hallucinated_evidence/test_pipeline.py \
       examples/04_healthcare_misdiagnosis/test_solution.py \
       examples/05_financial_cascade/test_solution.py \
       examples/06_eu_ai_act/test_solution.py \
       examples/07_pilot_failure/test_solution.py \
       examples/08_deleted_databases/test_pipeline.py -q

# Flagship runnable demos
python examples/09_prm_policy_repair/demo.py
python examples/p9_crash_recovery/p9_crash_recovery.py
python examples/p6_aml_kyc_investigation/p6_aml_kyc.py
```
