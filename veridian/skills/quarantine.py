"""
veridian.skills.quarantine
──────────────────────────
SkillQuarantine — sandbox + verification for imported skills.

External skills must pass through quarantine before entering the library:
  1. Non-empty steps check
  2. Tool safety scan (eval/exec/shell injection/blocked imports)
  3. Content scan for encoded attack patterns
  4. Provisional trust score assignment (starts low)

Skills cannot enter the library without full quarantine verification.
Trust only increases through verified production use.
"""

from __future__ import annotations

import ast
import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from veridian.skills.models import Skill

__all__ = ["SkillQuarantine", "QuarantineResult", "QuarantineStatus"]

log = logging.getLogger(__name__)

# ── Patterns for content scanning ────────────────────────────────────────────

_DANGEROUS_CALLS = frozenset({"eval", "exec", "compile", "__import__", "os.system"})

_ENCODED_ATTACK_PATTERN = re.compile(r"base64\s*[\-\.]\s*d|base64\.b64decode|atob\(", re.IGNORECASE)

_INJECTION_PATTERNS = [
    re.compile(r"IGNORE\s+(ALL\s+)?(PREVIOUS\s+)?INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r";\s*rm\s+\-rf\s+/", re.IGNORECASE),
    re.compile(r"subprocess\.call|subprocess\.Popen|os\.popen", re.IGNORECASE),
]


class QuarantineStatus(enum.Enum):
    """Outcome of quarantine evaluation."""

    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass
class QuarantineResult:
    """Result of skill quarantine evaluation."""

    skill_id: str = ""
    status: QuarantineStatus = QuarantineStatus.PENDING
    trust_score: float = 0.0
    violations: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "skill_id": self.skill_id,
            "status": self.status.value,
            "trust_score": round(self.trust_score, 4),
            "violations": self.violations,
            "checks_passed": self.checks_passed,
        }

    def to_markdown(self) -> str:
        """Generate quarantine report markdown."""
        lines = [
            f"# Quarantine Result — {self.skill_id}",
            "",
            f"**Status:** {self.status.value.upper()}",
            f"**Trust score:** {self.trust_score:.4f}",
            "",
        ]
        if self.violations:
            lines.append("## Violations")
            for v in self.violations:
                lines.append(f"- {v}")
            lines.append("")
        if self.checks_passed:
            lines.append("## Checks Passed")
            for c in self.checks_passed:
                lines.append(f"- {c}")
        lines.append("")
        return "\n".join(lines)


class SkillQuarantine:
    """Evaluates external skills before library admission.

    Runs tool safety, content scanning, and structure validation.
    Assigns a provisional trust score to approved skills.
    """

    def __init__(self, initial_trust_score: float = 0.1) -> None:
        self._initial_trust_score = initial_trust_score

    def evaluate(self, skill: Skill) -> QuarantineResult:
        """Run all quarantine checks on a skill."""
        violations: list[str] = []
        checks_passed: list[str] = []

        # Check 1: Non-empty steps
        if not skill.steps:
            violations.append("Skill has no steps")
            return QuarantineResult(
                skill_id=skill.id,
                status=QuarantineStatus.REJECTED,
                trust_score=0.0,
                violations=violations,
            )
        checks_passed.append("non_empty_steps")

        # Check 2: Tool safety — AST scan for dangerous calls
        tool_violations = self._check_tool_safety(skill)
        if tool_violations:
            violations.extend(tool_violations)
        else:
            checks_passed.append("tool_safety")

        # Check 3: Content scan for encoded attacks
        content_violations = self._check_content(skill)
        if content_violations:
            violations.extend(content_violations)
        else:
            checks_passed.append("content_scan")

        # Check 4: Injection pattern scan
        injection_violations = self._check_injection_patterns(skill)
        if injection_violations:
            violations.extend(injection_violations)
        else:
            checks_passed.append("injection_scan")

        if violations:
            return QuarantineResult(
                skill_id=skill.id,
                status=QuarantineStatus.REJECTED,
                trust_score=0.0,
                violations=violations,
                checks_passed=checks_passed,
            )

        return QuarantineResult(
            skill_id=skill.id,
            status=QuarantineStatus.APPROVED,
            trust_score=self._initial_trust_score,
            violations=[],
            checks_passed=checks_passed,
        )

    def _check_tool_safety(self, skill: Skill) -> list[str]:
        """AST-based check for dangerous function calls in step commands."""
        violations: list[str] = []
        for step in skill.steps:
            cmd = step.command
            if not cmd:
                continue
            # Check for dangerous Python calls via AST
            try:
                tree = ast.parse(cmd)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func_name = ""
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            func_name = node.func.attr
                        if func_name in _DANGEROUS_CALLS:
                            violations.append(
                                f"Dangerous call '{func_name}' in step: {step.description[:60]}"
                            )
            except SyntaxError:
                pass  # Not valid Python — check as shell command below

            # Shell command checks
            for call in _DANGEROUS_CALLS:
                already_found = [v.split("'")[1] for v in violations if "'" in v]
                if call in cmd and call not in ("compile",) and call not in already_found:
                    violations.append(
                        f"Dangerous pattern '{call}' in step command: {step.description[:60]}"
                    )
        return violations

    def _check_content(self, skill: Skill) -> list[str]:
        """Scan for encoded attack patterns."""
        violations: list[str] = []
        for step in skill.steps:
            cmd = step.command or ""
            desc = step.description or ""
            text = f"{cmd} {desc}"
            if _ENCODED_ATTACK_PATTERN.search(text):
                violations.append(f"Encoded attack pattern in step: {step.description[:60]}")
        return violations

    def _check_injection_patterns(self, skill: Skill) -> list[str]:
        """Scan for prompt injection and shell injection patterns."""
        violations: list[str] = []
        for step in skill.steps:
            cmd = step.command or ""
            desc = step.description or ""
            text = f"{cmd} {desc}"
            for pattern in _INJECTION_PATTERNS:
                if pattern.search(text):
                    violations.append(
                        f"Injection pattern detected in step: {step.description[:60]}"
                    )
                    break
        return violations
