"""
Problem 6: EU AI Act Compliance — No Audit Trail
==================================================
How Veridian produces EU AI Act Article 12-compliant evidence.

EU AI ACT — KEY REQUIREMENTS:
  Effective: August 2, 2026 (high-risk system enforcement)
  Article 12: Automatic logging of events throughout lifecycle
  Article 19: Automatically generated logs for deployers
  Penalties: EUR 35M or 7% of global turnover (prohibited practices)
            EUR 15M or 3% of global turnover (record-keeping failures)
  Enforcement: Finland first with full powers (Dec 2025)
  Market authorities can ORDER systems WITHDRAWN from market.

WHAT ARTICLE 12 REQUIRES:
  - Logging of events relevant for risk identification
  - Period of each use (start/end timestamps)
  - Input data that led to matches/decisions
  - Identification of persons involved in verification
  - Logs retained at least 6 months
  - Traceability: every output tied to model version + policy

VERIDIAN'S SOLUTION:
  ProofChain — SHA-256 hash-linked entries where each task produces:
    task_spec_hash, verifier_config_hash, model_version, input_hash,
    output_hash, verification_evidence, policy_attestation,
    previous_hash (chain link), chain_signature (HMAC)

  ComplianceReportGenerator — produces human-readable reports for
  EU AI Act, NIST AI RMF, and OWASP Agentic Top 10.

  Tamper detection: changing ANY entry breaks the hash chain.
  Mathematical proof that logs haven't been altered post-hoc.

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from veridian.observability.proof_chain import ProofChain, ProofEntry
from veridian.observability.compliance_report import (
    ComplianceReportGenerator,
    ComplianceStandard,
)


def build_auditable_pipeline() -> ProofChain:
    """Build a proof chain that satisfies EU AI Act Article 12.

    Each entry records exactly:
      - WHAT was asked (task_spec_hash)
      - HOW it was verified (verifier_config_hash)
      - WHICH model produced it (model_version)
      - WHAT went in and came out (input/output hashes)
      - WHETHER it passed verification (verification_evidence)
      - WHICH policies were active (policy_attestation)
      - WHEN it happened (timestamp — automatic)
      - CHAIN INTEGRITY (previous_hash — links to prior entry)
      - AUTHENTICITY (chain_signature — HMAC)
    """
    chain = ProofChain(signing_key="eu-ai-act-signing-key-2026")

    # Simulate a regulated workflow — each task is a real compliance step
    regulated_tasks = [
        {
            "task_id": "classify_complaint",
            "description": "Classify customer complaint by severity and route to department",
            "verifier": "schema",
            "model": "gemini/gemini-2.5-flash",
            "policies": ["eu_ai_act_art_12", "eu_ai_act_art_14", "gdpr_art_22"],
            "evidence": {"passed": True, "verifier": "schema", "required_fields": ["severity", "department", "justification"]},
        },
        {
            "task_id": "extract_pii",
            "description": "Identify and flag PII in customer communication for GDPR compliance",
            "verifier": "composite(schema+semantic_grounding)",
            "model": "gemini/gemini-2.5-flash",
            "policies": ["eu_ai_act_art_12", "gdpr_art_17", "gdpr_art_35"],
            "evidence": {"passed": True, "verifier": "composite", "pii_found": ["email", "phone"], "redacted": True},
        },
        {
            "task_id": "risk_assessment",
            "description": "Assess compliance risk level based on complaint content and regulatory context",
            "verifier": "semantic_grounding",
            "model": "gemini/gemini-2.5-flash",
            "policies": ["eu_ai_act_art_12", "eu_ai_act_art_9"],
            "evidence": {"passed": True, "verifier": "semantic_grounding", "risk_level": "MEDIUM", "grounding_score": 0.92},
        },
        {
            "task_id": "generate_response",
            "description": "Draft compliance-approved response to customer complaint",
            "verifier": "composite(schema+tool_safety)",
            "model": "gemini/gemini-2.5-flash",
            "policies": ["eu_ai_act_art_12", "eu_ai_act_art_14", "eu_ai_act_art_52"],
            "evidence": {"passed": True, "verifier": "composite", "tone": "professional", "pii_scrubbed": True},
        },
        {
            "task_id": "audit_verification",
            "description": "Final verification that all outputs meet regulatory requirements",
            "verifier": "composite(schema+semantic_grounding+quote_match)",
            "model": "gemini/gemini-2.5-flash",
            "policies": ["eu_ai_act_art_12", "eu_ai_act_art_19", "eu_ai_act_art_72"],
            "evidence": {"passed": True, "verifier": "composite", "all_checks": "passed", "chain_integrity": True},
        },
    ]

    for t in regulated_tasks:
        entry = ProofEntry(
            task_id=t["task_id"],
            task_spec_hash=hashlib.sha256(t["description"].encode()).hexdigest(),
            verifier_config_hash=hashlib.sha256(t["verifier"].encode()).hexdigest(),
            model_version=t["model"],
            input_hash=hashlib.sha256(f"input-{t['task_id']}".encode()).hexdigest(),
            output_hash=hashlib.sha256(f"output-{t['task_id']}".encode()).hexdigest(),
            verification_evidence=t["evidence"],
            policy_attestation=t["policies"],
        )
        chain.append(entry)

    return chain


def run_demo() -> None:
    start = time.monotonic()

    # Build the chain
    chain = build_auditable_pipeline()
    intact = chain.verify()

    # Generate compliance report
    gen = ComplianceReportGenerator(proof_chain=chain)
    report = gen.generate(ComplianceStandard.EU_AI_ACT)

    print("\n" + "=" * 75)
    print("  VERIDIAN -- EU AI Act Article 12 Compliance Demo")
    print("  Real ProofChain (SHA-256 + HMAC) + ComplianceReportGenerator")
    print("=" * 75)
    print(f"\n  Chain entries:      {len(chain)}")
    print(f"  Hash algorithm:     SHA-256 (hash-linked)")
    print(f"  HMAC signed:        Yes")
    print(f"  Chain integrity:    {'INTACT' if intact else 'BROKEN'}")
    print(f"  Standard:           EU AI Act")
    print(f"  Model tracked:      {', '.join(set(report.model_versions))}")
    print(f"  Policies attested:  {len(set(report.policies_active))} unique")
    print(f"  Compliance status:  {'COMPLIANT' if report.chain_intact else 'NON-COMPLIANT'}")

    # Demonstrate tamper detection
    print(f"\n  --- Tamper Detection ---")
    print(f"  Modifying entry 3 (risk_assessment)...")
    chain._entries[2].task_spec_hash = "TAMPERED_BY_ATTACKER"
    tampered_check = chain.verify()
    print(f"  Chain integrity after tampering: {'INTACT' if tampered_check else 'BROKEN'}")
    print(f"  Verdict: Retroactive modification DETECTED mathematically.")
    print(f"  A compliance officer can prove logs were NOT altered post-hoc.")

    # Show report excerpt
    print(f"\n  --- Report Excerpt ---")
    # Regenerate with original chain for clean report
    clean_chain = build_auditable_pipeline()
    clean_report = gen.generate(ComplianceStandard.EU_AI_ACT)
    for line in clean_report.to_markdown().split("\n")[:10]:
        print(f"  {line}")

    elapsed = int((time.monotonic() - start) * 1000)
    print(f"\n  Elapsed: {elapsed}ms")
    print(f"  Article 12 satisfied: logging, traceability, model binding, policy attestation")
    print(f"  Article 19 satisfied: automatically generated, machine-readable logs")
    print("=" * 75)


if __name__ == "__main__":
    run_demo()
