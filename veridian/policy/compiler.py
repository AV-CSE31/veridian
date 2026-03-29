"""
veridian.policy.compiler
─────────────────────────
PolicyCompiler: YAML/JSON policy definitions → deterministic Python verifiers.

Supported operators
───────────────────
  not_contains_pattern   field value does NOT match regex pattern
  contains_pattern       field value DOES match regex pattern
  not_contains           field value does not contain literal substring
  contains               field value contains literal substring
  equals                 field value == expected
  not_equals             field value != expected
  length_gt              len(field value) > int(expected)
  length_lt              len(field value) < int(expected)
  is_not_empty           field value is truthy (non-empty string/dict/list)
  is_empty               field value is falsy

Field paths
───────────
  raw_output             result.raw_output
  structured             result.structured (the whole dict)
  structured.KEY         result.structured.get("KEY")
  artifacts              result.artifacts (list)
"""

from __future__ import annotations

import re
from typing import Any

from veridian.core.exceptions import PolicyCompilationError
from veridian.core.task import Task, TaskResult
from veridian.policy.models import Policy, PolicyCheck, PolicyRule, PolicySeverity
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = ["PolicyCompiler"]

_SUPPORTED_OPERATORS = frozenset(
    [
        "not_contains_pattern",
        "contains_pattern",
        "not_contains",
        "contains",
        "equals",
        "not_equals",
        "length_gt",
        "length_lt",
        "is_not_empty",
        "is_empty",
    ]
)


# ─────────────────────────────────────────────────────────────────────────────
# Field extractor
# ─────────────────────────────────────────────────────────────────────────────


def _extract_field(result: TaskResult, field_path: str) -> Any:
    """Extract a value from a TaskResult by field path."""
    if field_path == "raw_output":
        return result.raw_output
    if field_path == "structured":
        return result.structured
    if field_path == "artifacts":
        return result.artifacts
    if field_path.startswith("structured."):
        key = field_path[len("structured."):]
        return result.structured.get(key)
    # Default: return as-is (callers should handle None)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Operator evaluator
# ─────────────────────────────────────────────────────────────────────────────


def _evaluate_check(check: PolicyCheck, result: TaskResult) -> bool:
    """
    Evaluate a single PolicyCheck against a TaskResult.

    Returns True iff the check passes (no violation).
    """
    field_value = _extract_field(result, check.field)
    op = check.operator
    expected = check.value

    str_value = str(field_value) if field_value is not None else ""

    if op == "not_contains_pattern":
        return re.search(expected, str_value) is None
    if op == "contains_pattern":
        return re.search(expected, str_value) is not None
    if op == "not_contains":
        return expected not in str_value
    if op == "contains":
        return expected in str_value
    if op == "equals":
        return str(field_value) == expected
    if op == "not_equals":
        return str(field_value) != expected
    if op == "length_gt":
        try:
            threshold = int(expected)
        except ValueError:
            return False
        return len(str_value) > threshold
    if op == "length_lt":
        try:
            threshold = int(expected)
        except ValueError:
            return False
        return len(str_value) < threshold
    if op == "is_not_empty":
        return bool(field_value)
    if op == "is_empty":
        return not bool(field_value)

    # Should never reach here if operators are validated at compile time
    return True  # pragma: no cover


# ─────────────────────────────────────────────────────────────────────────────
# Verifier factory
# ─────────────────────────────────────────────────────────────────────────────


def _make_verifier_class(policy: Policy) -> type[BaseVerifier]:
    """
    Dynamically create a BaseVerifier subclass that evaluates the given Policy.

    Uses Python's type() builtin to generate the class at runtime — no exec().
    The verify() method is a closure over the policy's rules, capturing all
    checks at class-creation time.
    """
    # Capture at class-creation time (not at call time)
    policy_rules: list[PolicyRule] = list(policy.rules)
    policy_id = policy.policy_id
    policy_desc = policy.description

    def verify(self: BaseVerifier, task: Task, result: TaskResult) -> VerificationResult:
        violations: list[str] = []
        for rule in policy_rules:
            rule_violated = False
            for check in rule.checks:
                if not _evaluate_check(check, result):
                    violations.append(check.error_message)
                    rule_violated = True
                    if rule.severity == PolicySeverity.BLOCKING:
                        # Stop at first failing check in a blocking rule
                        break
            if rule_violated and rule.severity == PolicySeverity.BLOCKING:
                # Return immediately on first blocking violation
                return VerificationResult(
                    passed=False,
                    error=violations[0],
                    evidence={"policy_id": policy_id, "violations": violations},
                )
        if violations:
            # Warning-level violations — still fail but with all violations
            return VerificationResult(
                passed=False,
                error="; ".join(violations[:3]),
                evidence={"policy_id": policy_id, "violations": violations},
            )
        return VerificationResult(
            passed=True,
            evidence={"policy_id": policy_id, "rules_evaluated": len(policy_rules)},
        )

    verifier_cls: type[BaseVerifier] = type(
        f"Policy_{policy_id.replace('-', '_').replace('.', '_')}",
        (BaseVerifier,),
        {
            "id": f"policy_{policy_id}",
            "description": policy_desc,
            "verify": verify,
        },
    )
    return verifier_cls


# ─────────────────────────────────────────────────────────────────────────────
# Compiler
# ─────────────────────────────────────────────────────────────────────────────


class PolicyCompiler:
    """
    Compiles YAML or Policy model objects into instantiable BaseVerifier classes.

    Usage::

        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(yaml_str)
        verifier = verifier_cls()
        result = verifier.verify(task, task_result)
    """

    def compile(self, policy: Policy) -> type[BaseVerifier]:
        """
        Compile a Policy model object into a verifier class.

        Validates all operators before generating the class.
        Raises PolicyCompilationError on any invalid operator.
        """
        self._validate_operators(policy)
        return _make_verifier_class(policy)

    def compile_yaml(self, yaml_text: str) -> type[BaseVerifier]:
        """
        Parse a YAML (or JSON) policy definition and compile it.

        Raises PolicyCompilationError on parse or validation errors.
        """
        policy = self._parse_yaml(yaml_text)
        return self.compile(policy)

    # ── private ────────────────────────────────────────────────────────────────

    def _parse_yaml(self, text: str) -> Policy:
        """Parse YAML/JSON text into a Policy object."""
        try:
            import yaml  # type: ignore[import-untyped]

            raw = yaml.safe_load(text)
        except Exception as exc:
            raise PolicyCompilationError(
                policy_id="<unknown>",
                reason=f"YAML parse error: {exc}",
            ) from exc

        if not isinstance(raw, dict):
            raise PolicyCompilationError(
                policy_id="<unknown>",
                reason="Policy must be a YAML mapping at the top level",
            )

        policy_id = raw.get("policy_id", "<unknown>")

        try:
            rules: list[PolicyRule] = []
            for r in raw.get("rules", []):
                checks = [
                    PolicyCheck(
                        field=c["field"],
                        operator=c["operator"],
                        value=str(c.get("value", "")),
                        error_message=c.get("error_message", "Policy check failed"),
                    )
                    for c in r.get("checks", [])
                ]
                rules.append(
                    PolicyRule(
                        rule_id=r["rule_id"],
                        description=r.get("description", ""),
                        severity=PolicySeverity(r.get("severity", "blocking")),
                        checks=checks,
                    )
                )
            return Policy(
                policy_id=policy_id,
                version=str(raw.get("version", "1.0")),
                description=raw.get("description", ""),
                framework=raw.get("framework", "custom"),
                rules=rules,
            )
        except Exception as exc:
            raise PolicyCompilationError(
                policy_id=policy_id,
                reason=f"Policy structure error: {exc}",
            ) from exc

    def _validate_operators(self, policy: Policy) -> None:
        """Raise PolicyCompilationError if any check uses an unsupported operator."""
        for rule in policy.rules:
            for check in rule.checks:
                if check.operator not in _SUPPORTED_OPERATORS:
                    raise PolicyCompilationError(
                        policy_id=policy.policy_id,
                        reason=(
                            f"Unsupported operator {check.operator!r} in rule "
                            f"{rule.rule_id!r}. "
                            f"Supported: {sorted(_SUPPORTED_OPERATORS)}"
                        ),
                    )
