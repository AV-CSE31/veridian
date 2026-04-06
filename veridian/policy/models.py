"""
veridian.policy.models
───────────────────────
Policy-as-Code domain models (F2.5).

A Policy is a versioned, hash-pinned collection of Rules.
Each Rule has one or more Checks (field + operator + expected value).
Checks are evaluated deterministically — no LLM calls.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PolicySeverity",
    "PolicyCheck",
    "PolicyRule",
    "Policy",
    "BUILTIN_POLICIES",
]


class PolicySeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class PolicyCheck(BaseModel):
    """A single field-level check within a policy rule."""

    model_config = ConfigDict(frozen=True)

    field: str  # e.g. "raw_output", "structured.status"
    operator: str  # e.g. "not_contains_pattern", "equals", "is_not_empty"
    value: str  # expected value or pattern
    error_message: str


class PolicyRule(BaseModel):
    """A named rule containing one or more checks."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    description: str
    severity: PolicySeverity
    checks: list[PolicyCheck] = Field(default_factory=list)


class Policy(BaseModel):
    """
    A versioned, hash-pinned policy definition.

    Policies are compiled to deterministic Python verifiers by PolicyCompiler.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: str
    version: str
    description: str
    framework: str  # e.g. "gdpr", "hipaa", "sox", "eu_ai_act", "owasp"
    rules: list[PolicyRule] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON representation (excluding metadata)."""
        canonical = json.dumps(
            {
                "policy_id": self.policy_id,
                "version": self.version,
                "description": self.description,
                "framework": self.framework,
                "rules": [
                    {
                        "rule_id": r.rule_id,
                        "severity": r.severity,
                        "checks": [
                            {"field": c.field, "operator": c.operator, "value": c.value}
                            for c in r.checks
                        ],
                    }
                    for r in self.rules
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Built-in policy templates (10 policies across 5 compliance frameworks)
# ─────────────────────────────────────────────────────────────────────────────


def _rule(
    rule_id: str,
    desc: str,
    severity: PolicySeverity,
    checks: list[PolicyCheck],
) -> PolicyRule:
    return PolicyRule(rule_id=rule_id, description=desc, severity=severity, checks=checks)


def _check(field: str, op: str, value: str, error: str) -> PolicyCheck:
    return PolicyCheck(field=field, operator=op, value=value, error_message=error)


_BLOCKING = PolicySeverity.BLOCKING
_WARNING = PolicySeverity.WARNING

BUILTIN_POLICIES: list[Policy] = [
    # ── GDPR ──────────────────────────────────────────────────────────────────
    Policy(
        policy_id="gdpr_no_pii_output",
        version="1.0",
        description="GDPR: No personally identifiable information in output",
        framework="gdpr",
        rules=[
            _rule(
                "no_email",
                "Output must not contain email addresses",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                        "GDPR violation: email address detected in output",
                    )
                ],
            ),
            _rule(
                "no_phone",
                "Output must not contain phone numbers",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\+?[\d\s\-\(\)]{10,}",
                        "GDPR violation: phone number detected in output",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="gdpr_output_not_empty",
        version="1.0",
        description="GDPR: AI output must be substantive (non-empty) to be auditable",
        framework="gdpr",
        rules=[
            _rule(
                "non_empty",
                "raw_output must not be empty",
                _BLOCKING,
                [_check("raw_output", "is_not_empty", "", "GDPR: output is empty — not auditable")],
            ),
        ],
    ),
    # ── HIPAA ─────────────────────────────────────────────────────────────────
    Policy(
        policy_id="hipaa_no_phi_output",
        version="1.0",
        description="HIPAA: No protected health information in AI output",
        framework="hipaa",
        rules=[
            _rule(
                "no_ssn",
                "Output must not contain US Social Security Numbers",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\b\d{3}-\d{2}-\d{4}\b",
                        "HIPAA violation: SSN pattern detected in output",
                    )
                ],
            ),
            _rule(
                "no_dob",
                "Output must not contain DOB in common formats",
                _WARNING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\b(?:DOB|Date of Birth|born)\s*:?\s*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
                        "HIPAA warning: date-of-birth pattern detected",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="hipaa_no_patient_names",
        version="1.0",
        description="HIPAA: Output must not reference patient names with clinical data",
        framework="hipaa",
        rules=[
            _rule(
                "no_patient_prefix",
                "Output must not start with 'Patient:' or 'Patient Name:'",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"(?i)patient\s*(name)?\s*:",
                        "HIPAA violation: patient name reference detected",
                    )
                ],
            ),
        ],
    ),
    # ── SOX ───────────────────────────────────────────────────────────────────
    Policy(
        policy_id="sox_no_unapproved_financial",
        version="1.0",
        description="SOX: Financial figures must not appear without structured validation",
        framework="sox",
        rules=[
            _rule(
                "structured_not_empty",
                "Structured output must be present for financial tasks",
                _BLOCKING,
                [
                    _check(
                        "structured",
                        "is_not_empty",
                        "",
                        "SOX violation: financial output must include structured fields",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="sox_output_length_reasonable",
        version="1.0",
        description="SOX: Agent output must be substantive (not a trivially short response)",
        framework="sox",
        rules=[
            _rule(
                "min_output_length",
                "raw_output must be at least 10 characters",
                _WARNING,
                [
                    _check(
                        "raw_output",
                        "length_gt",
                        "10",
                        "SOX warning: output is suspiciously short for a financial analysis",
                    )
                ],
            ),
        ],
    ),
    # ── EU AI Act ─────────────────────────────────────────────────────────────
    Policy(
        policy_id="eu_ai_act_transparency",
        version="1.0",
        description="EU AI Act Art.13: High-risk AI must disclose it is AI-generated",
        framework="eu_ai_act",
        rules=[
            _rule(
                "no_impersonation",
                "Output must not claim to be human-authored",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"(?i)(i am a human|written by a human|not an ai)",
                        "EU AI Act violation: output falsely claims human authorship",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="eu_ai_act_no_deceptive_content",
        version="1.0",
        description="EU AI Act Art.5: Prohibits subliminal or deceptive AI techniques",
        framework="eu_ai_act",
        rules=[
            _rule(
                "no_hidden_instructions",
                "Output must not contain hidden Unicode control characters",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]",
                        "EU AI Act violation: hidden control characters detected in output",
                    )
                ],
            ),
        ],
    ),
    # ── OWASP ASI ─────────────────────────────────────────────────────────────
    Policy(
        policy_id="owasp_asi_no_shell_injection",
        version="1.0",
        description="OWASP ASI03: Detect shell injection patterns in AI output",
        framework="owasp",
        rules=[
            _rule(
                "no_shell_true",
                "Output must not contain shell=True patterns",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"shell\s*=\s*True",
                        "OWASP ASI03: shell=True injection pattern detected",
                    )
                ],
            ),
            _rule(
                "no_os_system",
                "Output must not contain os.system() calls",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\bos\.system\s*\(",
                        "OWASP ASI03: os.system() call detected in output",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="owasp_asi_no_eval_exec",
        version="1.0",
        description="OWASP ASI03: Detect eval/exec usage in AI-generated code",
        framework="owasp",
        rules=[
            _rule(
                "no_eval",
                "Output must not contain eval() calls",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\beval\s*\(",
                        "OWASP ASI03: eval() detected — dynamic code execution risk",
                    )
                ],
            ),
            _rule(
                "no_exec",
                "Output must not contain exec() calls",
                _BLOCKING,
                [
                    _check(
                        "raw_output",
                        "not_contains_pattern",
                        r"\bexec\s*\(",
                        "OWASP ASI03: exec() detected — dynamic code execution risk",
                    )
                ],
            ),
        ],
    ),
    Policy(
        policy_id="owasp_asi_output_validation",
        version="1.0",
        description="OWASP ASI: AI output must include structured verification fields",
        framework="owasp",
        rules=[
            _rule(
                "has_summary",
                "Structured output should not be entirely empty",
                _WARNING,
                [
                    _check(
                        "raw_output",
                        "is_not_empty",
                        "",
                        "OWASP ASI: output is empty — cannot validate agent response",
                    )
                ],
            ),
        ],
    ),
]
