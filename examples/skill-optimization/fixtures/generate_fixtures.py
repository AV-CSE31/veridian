"""
Generate synthetic fixture data for the Veridian × SkillNet × AutoResearch
experiment suite.

Generates:
  - 500 skills across 3 domains (legal, compliance, code)
  - 200 queries (100 in-distribution, 100 out-of-distribution)

All data is deterministic given RANDOM_SEED and written atomically to:
  examples/fixtures/data/skills.json
  examples/fixtures/data/queries.json

Usage:
  python examples/fixtures/generate_fixtures.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import DATA_DIR, RANDOM_SEED

# ── Domain definitions ────────────────────────────────────────────────────────

DOMAINS = {
    "legal": {
        "skill_templates": [
            "Review {doc_type} for {clause_type} clauses",
            "Extract {field} from {doc_type} agreement",
            "Assess {clause_type} risk in {contract_party} contract",
            "Identify {clause_type} obligations for {contract_party}",
            "Verify {field} compliance with {regulation}",
        ],
        "doc_types": [
            "NDA", "MSA", "SOW", "lease", "employment", "vendor", "SLA",
            "license", "partnership", "acquisition",
        ],
        "clause_types": [
            "indemnification", "limitation_of_liability", "termination",
            "change_of_control", "non_compete", "confidentiality", "IP_ownership",
            "force_majeure", "governing_law", "dispute_resolution",
        ],
        "fields": [
            "payment_terms", "notice_period", "liability_cap", "renewal_terms",
            "audit_rights", "data_residency", "SLA_credits",
        ],
        "regulations": ["GDPR", "CCPA", "SOX", "HIPAA", "PCI-DSS"],
        "parties": ["vendor", "customer", "partner", "employee", "contractor"],
        "risk_levels": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "output_schema": {
            "required": ["clause_type", "risk_level", "decision", "quote"],
            "optional": ["page_number", "reasoning"],
        },
    },
    "compliance": {
        "skill_templates": [
            "Evaluate {control} control for {framework} compliance",
            "Assess {domain_area} gap against {framework} requirement {control_id}",
            "Review {evidence_type} for {framework} {control_id} evidence",
            "Check {process} process against {framework} {control_id}",
            "Audit {system} system for {framework} compliance",
        ],
        "controls": [
            "CC6.1", "CC7.2", "CC8.1", "A1.1", "A1.2", "PI1.1", "C1.1",
            "CC5.1", "CC5.2", "CC9.1", "CC9.2",
        ],
        "frameworks": ["SOC2", "ISO27001", "NIST-CSF", "PCI-DSS", "HIPAA"],
        "domain_areas": [
            "access_control", "encryption", "audit_logging", "incident_response",
            "change_management", "vendor_management", "data_classification",
        ],
        "evidence_types": [
            "policy_document", "system_screenshot", "log_export", "interview_notes",
            "configuration_file",
        ],
        "processes": [
            "onboarding", "offboarding", "patch_management", "backup_restore",
            "pen_testing", "code_review",
        ],
        "systems": [
            "AWS_IAM", "GitHub", "Slack", "Okta", "PagerDuty", "Datadog",
        ],
        "statuses": ["compliant", "partial", "gap", "not_applicable"],
        "output_schema": {
            "required": ["status", "evidence_source", "control_id"],
            "optional": ["evidence_quote", "remediation"],
        },
    },
    "code": {
        "skill_templates": [
            "Migrate {module} from {old_version} to {new_version}",
            "Refactor {pattern} in {module} to use {new_pattern}",
            "Add {test_type} tests for {module}",
            "Fix {issue_type} bug in {module}",
            "Optimize {operation} performance in {module}",
        ],
        "modules": [
            "auth", "payments", "notifications", "search", "storage",
            "cache", "queue", "scheduler", "api_gateway", "data_pipeline",
        ],
        "old_versions": ["Python 2.7", "Python 3.8", "Django 3.x", "Flask 1.x"],
        "new_versions": ["Python 3.11", "Python 3.12", "Django 5.x", "Flask 3.x"],
        "patterns": [
            "callback_chains", "global_state", "sync_blocking_calls",
            "raw_SQL_queries", "hardcoded_secrets",
        ],
        "new_patterns": [
            "async/await", "dependency_injection", "ORM_queries",
            "env_secrets", "event_driven",
        ],
        "test_types": ["unit", "integration", "e2e", "property-based"],
        "issue_types": [
            "memory_leak", "race_condition", "SQL_injection", "XSS",
            "n+1_query", "deadlock",
        ],
        "operations": [
            "database_query", "file_IO", "JSON_serialization", "HTTP_requests",
        ],
        "output_schema": {
            "required": ["status", "test_command", "exit_code"],
            "optional": ["changed_files", "performance_improvement_pct"],
        },
    },
}

OOD_DRIFT_PATTERNS = [
    # Out-of-distribution patterns: cross-domain terminology contamination
    "Evaluate {concept} using {wrong_domain} methodology",
    "Apply {wrong_domain} risk framework to {concept} assessment",
    "Process {concept} via {wrong_domain} pipeline",
]


def _atomic_write(path: Path, data: object) -> None:
    """Write JSON atomically using temp-file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        json.dump(data, f, indent=2)
        tmp = f.name
    os.replace(tmp, str(path))


def generate_skill(
    skill_idx: int,
    domain: str,
    cfg: dict,
    rng: random.Random,
) -> dict:
    """Generate a single skill record."""
    template = rng.choice(cfg["skill_templates"])

    # Fill template placeholders
    kwargs: dict = {}
    if "{doc_type}" in template:
        kwargs["doc_type"] = rng.choice(cfg.get("doc_types", ["document"]))
    if "{clause_type}" in template:
        kwargs["clause_type"] = rng.choice(cfg.get("clause_types", ["standard"]))
    if "{field}" in template:
        kwargs["field"] = rng.choice(cfg.get("fields", ["field"]))
    if "{regulation}" in template:
        kwargs["regulation"] = rng.choice(cfg.get("regulations", ["regulation"]))
    if "{contract_party}" in template:
        kwargs["contract_party"] = rng.choice(cfg.get("parties", ["party"]))
    if "{control}" in template:
        kwargs["control"] = rng.choice(cfg.get("controls", ["CC1.1"]))
    if "{framework}" in template:
        kwargs["framework"] = rng.choice(cfg.get("frameworks", ["SOC2"]))
    if "{control_id}" in template:
        kwargs["control_id"] = rng.choice(cfg.get("controls", ["CC1.1"]))
    if "{domain_area}" in template:
        kwargs["domain_area"] = rng.choice(cfg.get("domain_areas", ["access"]))
    if "{evidence_type}" in template:
        kwargs["evidence_type"] = rng.choice(cfg.get("evidence_types", ["document"]))
    if "{process}" in template:
        kwargs["process"] = rng.choice(cfg.get("processes", ["process"]))
    if "{system}" in template:
        kwargs["system"] = rng.choice(cfg.get("systems", ["system"]))
    if "{module}" in template:
        kwargs["module"] = rng.choice(cfg.get("modules", ["module"]))
    if "{old_version}" in template:
        kwargs["old_version"] = rng.choice(cfg.get("old_versions", ["v1"]))
    if "{new_version}" in template:
        kwargs["new_version"] = rng.choice(cfg.get("new_versions", ["v2"]))
    if "{pattern}" in template:
        kwargs["pattern"] = rng.choice(cfg.get("patterns", ["old_pattern"]))
    if "{new_pattern}" in template:
        kwargs["new_pattern"] = rng.choice(cfg.get("new_patterns", ["new_pattern"]))
    if "{test_type}" in template:
        kwargs["test_type"] = rng.choice(cfg.get("test_types", ["unit"]))
    if "{issue_type}" in template:
        kwargs["issue_type"] = rng.choice(cfg.get("issue_types", ["bug"]))
    if "{operation}" in template:
        kwargs["operation"] = rng.choice(cfg.get("operations", ["operation"]))

    name = template.format(**kwargs)

    # Confidence and quality signals
    base_confidence = rng.uniform(0.55, 0.98)
    quality_tier = "high" if base_confidence > 0.80 else "medium" if base_confidence > 0.65 else "low"

    schema = cfg.get("output_schema", {"required": [], "optional": []})

    # Simulate valid vs invalid structured output
    has_hallucination = rng.random() < 0.12  # 12% have planted inconsistencies

    structured: dict = {}
    if domain == "legal":
        risk = rng.choice(cfg.get("risk_levels", ["LOW"]))
        decision = "ALLOW" if risk in ("LOW", "MEDIUM") else rng.choice(["ALLOW", "ESCALATE", "FLAG"])
        structured = {
            "clause_type": kwargs.get("clause_type", "standard"),
            "risk_level": risk,
            "decision": decision,
            "quote": f"Sample clause text for {kwargs.get('clause_type', 'clause')}...",
            "page_number": rng.randint(1, 50),
        }
        # Plant inconsistency: status=compliant but violated_policies non-empty
        if has_hallucination:
            structured["status"] = "compliant"
            structured["violated_policies"] = ["policy_1", "policy_2"]  # contradiction!
    elif domain == "compliance":
        status = rng.choice(cfg.get("statuses", ["compliant"]))
        structured = {
            "status": status,
            "evidence_source": rng.choice(cfg.get("evidence_types", ["document"])),
            "control_id": kwargs.get("control", "CC1.1"),
            "evidence_quote": f"Evidence text for control {kwargs.get('control', 'CC1.1')}",
        }
        if has_hallucination:
            structured["status"] = "compliant"
            structured["violated_policies"] = ["missing_control"]  # contradiction!
    else:  # code
        structured = {
            "status": rng.choice(["passed", "failed", "skipped"]),
            "test_command": f"pytest tests/test_{kwargs.get('module', 'module')}.py -v",
            "exit_code": rng.choice([0, 0, 0, 1]),  # mostly 0
            "changed_files": [f"src/{kwargs.get('module', 'module')}.py"],
        }

    return {
        "id": f"skill_{domain}_{skill_idx:04d}",
        "name": name,
        "domain": domain,
        "description": f"Automated skill: {name}",
        "confidence": round(base_confidence, 4),
        "quality_tier": quality_tier,
        "output_schema": schema,
        "structured_output": structured,
        "has_hallucination": has_hallucination,
        "trust_score": round(rng.uniform(0.60, 0.99), 4),
        "retry_count": rng.randint(0, 3),
        "created_epoch": rng.randint(1, 100),  # relative creation order
    }


def generate_query(
    query_idx: int,
    distribution: str,
    all_domains: list[str],
    rng: random.Random,
) -> dict:
    """Generate a single query record."""
    domain = rng.choice(all_domains)
    cfg = DOMAINS[domain]

    if distribution == "in_dist":
        # In-distribution: well-formed query within domain
        template = rng.choice(cfg["skill_templates"])
        kwargs: dict = {}
        for placeholder in [
            "doc_type", "clause_type", "field", "regulation", "contract_party",
            "control", "framework", "control_id", "domain_area", "evidence_type",
            "process", "system", "module", "old_version", "new_version",
            "pattern", "new_pattern", "test_type", "issue_type", "operation",
        ]:
            if f"{{{placeholder}}}" in template:
                choices_key = placeholder + "s"
                kwargs[placeholder] = rng.choice(
                    cfg.get(choices_key, [placeholder])
                )
        query_text = template.format(**kwargs)
        expected_domain = domain
        is_ambiguous = False
        relevance_score = round(rng.uniform(0.75, 0.99), 4)
    else:
        # Out-of-distribution: cross-domain contamination or novel phrasing
        wrong_domain = rng.choice([d for d in all_domains if d != domain])
        concepts = {
            "legal": rng.choice(DOMAINS["legal"].get("clause_types", ["clause"])),
            "compliance": rng.choice(DOMAINS["compliance"].get("controls", ["CC1.1"])),
            "code": rng.choice(DOMAINS["code"].get("modules", ["module"])),
        }
        template = rng.choice(OOD_DRIFT_PATTERNS)
        query_text = template.format(
            concept=concepts[domain],
            wrong_domain=wrong_domain,
        )
        expected_domain = domain
        is_ambiguous = True
        relevance_score = round(rng.uniform(0.35, 0.70), 4)

    # Expected structured output for this query
    ground_truth: dict = {}
    if domain == "legal":
        risk = rng.choice(DOMAINS["legal"]["risk_levels"])
        ground_truth = {
            "risk_level": risk,
            "decision": "ALLOW" if risk in ("LOW", "MEDIUM") else "ESCALATE",
        }
    elif domain == "compliance":
        ground_truth = {
            "status": rng.choice(DOMAINS["compliance"]["statuses"]),
        }
    else:
        ground_truth = {
            "status": rng.choice(["passed", "failed"]),
        }

    return {
        "id": f"query_{distribution}_{query_idx:04d}",
        "text": query_text,
        "domain": expected_domain,
        "distribution": distribution,
        "is_ambiguous": is_ambiguous,
        "relevance_score": relevance_score,
        "ground_truth": ground_truth,
    }


def generate_all(seed: int = RANDOM_SEED) -> tuple[list[dict], list[dict]]:
    """Generate 500 skills and 200 queries. Returns (skills, queries)."""
    rng = random.Random(seed)
    domain_names = list(DOMAINS.keys())
    n_skills_per_domain = 500 // len(domain_names)  # ~166 each

    skills: list[dict] = []
    idx = 0
    for domain_name, cfg in DOMAINS.items():
        count = n_skills_per_domain
        # Make last domain absorb rounding remainder
        if domain_name == domain_names[-1]:
            count = 500 - len(skills)
        for _j in range(count):
            skills.append(generate_skill(idx, domain_name, cfg, rng))
            idx += 1

    # 100 in-dist + 100 OOD queries
    queries: list[dict] = []
    for i in range(100):
        queries.append(generate_query(i, "in_dist", domain_names, rng))
    for i in range(100):
        queries.append(generate_query(i, "ood", domain_names, rng))

    rng.shuffle(queries)  # mix distributions

    return skills, queries


def main() -> None:
    print(f"Generating fixtures -> {DATA_DIR}")
    skills, queries = generate_all()

    skills_path = DATA_DIR / "skills.json"
    queries_path = DATA_DIR / "queries.json"

    _atomic_write(skills_path, skills)
    _atomic_write(queries_path, queries)

    n_domains = len({s["domain"] for s in skills})
    n_hallucinations = sum(1 for s in skills if s["has_hallucination"])
    n_in_dist = sum(1 for q in queries if q["distribution"] == "in_dist")
    n_ood = sum(1 for q in queries if q["distribution"] == "ood")

    print(f"  Skills : {len(skills):,} across {n_domains} domains")
    print(f"    Planted hallucinations : {n_hallucinations} ({n_hallucinations/len(skills)*100:.1f}%)")
    print(f"  Queries: {len(queries)} ({n_in_dist} in-dist, {n_ood} OOD)")
    print(f"  Written: {skills_path}")
    print(f"  Written: {queries_path}")


if __name__ == "__main__":
    main()
