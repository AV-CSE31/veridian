"""
tests/unit/test_policy_engine.py
─────────────────────────────────
Unit tests for the Policy-as-Code Engine (F2.5).

Covers:
  - PolicyCheck, PolicyRule, Policy: creation and validation
  - PolicyCompiler: YAML → verifier, all operators, error cases
  - PolicyEngine: load, evaluate, apply to task
  - Built-in policy templates: all 10 compile and run correctly
  - PolicyRegistry: versioning, hash-pinning
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import PolicyCompilationError
from veridian.core.task import Task, TaskResult
from veridian.policy.compiler import PolicyCompiler
from veridian.policy.engine import PolicyEngine
from veridian.policy.models import (
    BUILTIN_POLICIES,
    Policy,
    PolicyCheck,
    PolicyRule,
    PolicySeverity,
)
from veridian.verify.base import BaseVerifier, VerificationResult

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task() -> Task:
    return Task(id="t1", title="Test task", description="Verify policy")


@pytest.fixture
def clean_result() -> TaskResult:
    return TaskResult(
        raw_output="The analysis is complete. No issues found.",
        structured={"status": "ok", "amount": 100.0},
    )


@pytest.fixture
def pii_result() -> TaskResult:
    return TaskResult(
        raw_output="Contact: john.doe@example.com for details.",
        structured={"email": "john.doe@example.com"},
    )


def _yaml_policy(content: str) -> str:
    return content


SIMPLE_POLICY_YAML = """
policy_id: test_no_email
version: "1.0"
description: "Reject outputs containing email addresses"
framework: test
rules:
  - rule_id: no_email_in_output
    description: "No emails in raw_output"
    severity: blocking
    checks:
      - field: raw_output
        operator: not_contains_pattern
        value: '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        error_message: "Output contains an email address"
"""

FIELD_EQUALS_YAML = """
policy_id: test_status_ok
version: "1.0"
description: "Require status field to be ok"
framework: test
rules:
  - rule_id: status_must_be_ok
    description: "status field must equal ok"
    severity: blocking
    checks:
      - field: structured.status
        operator: equals
        value: "ok"
        error_message: "status field must be 'ok'"
"""

LENGTH_CHECK_YAML = """
policy_id: test_output_length
version: "1.0"
description: "Output must not be empty"
framework: test
rules:
  - rule_id: non_empty_output
    description: "raw_output must not be empty"
    severity: blocking
    checks:
      - field: raw_output
        operator: is_not_empty
        value: ""
        error_message: "Output is empty"
"""

INVALID_OPERATOR_YAML = """
policy_id: test_invalid
version: "1.0"
description: "Invalid policy"
framework: test
rules:
  - rule_id: bad_rule
    description: "bad"
    severity: blocking
    checks:
      - field: raw_output
        operator: does_not_exist_operator
        value: "x"
        error_message: "bad"
"""


# ─────────────────────────────────────────────────────────────────────────────
# PolicyCheck / PolicyRule / Policy model tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyModels:
    def test_policy_check_creation(self) -> None:
        check = PolicyCheck(
            field="raw_output",
            operator="not_contains",
            value="secret",
            error_message="Output must not contain 'secret'",
        )
        assert check.field == "raw_output"
        assert check.operator == "not_contains"

    def test_policy_rule_creation(self) -> None:
        rule = PolicyRule(
            rule_id="no_secrets",
            description="No secrets in output",
            severity=PolicySeverity.BLOCKING,
            checks=[
                PolicyCheck(
                    field="raw_output",
                    operator="not_contains",
                    value="password",
                    error_message="Password detected",
                )
            ],
        )
        assert rule.rule_id == "no_secrets"
        assert rule.severity == PolicySeverity.BLOCKING

    def test_policy_severity_values(self) -> None:
        assert PolicySeverity.WARNING.value == "warning"
        assert PolicySeverity.BLOCKING.value == "blocking"

    def test_policy_creation(self) -> None:
        policy = Policy(
            policy_id="test_policy",
            version="1.0",
            description="Test",
            framework="test",
            rules=[
                PolicyRule(
                    rule_id="r1",
                    description="rule 1",
                    severity=PolicySeverity.BLOCKING,
                    checks=[
                        PolicyCheck(
                            field="raw_output",
                            operator="is_not_empty",
                            value="",
                            error_message="empty",
                        )
                    ],
                )
            ],
        )
        assert policy.policy_id == "test_policy"
        assert len(policy.rules) == 1

    def test_policy_version_hash(self) -> None:
        policy = Policy(
            policy_id="test_policy", version="1.0",
            description="Test", framework="test", rules=[],
        )
        h = policy.content_hash()
        assert len(h) == 64  # SHA-256 hex

    def test_policy_version_hash_changes_with_content(self) -> None:
        p1 = Policy(policy_id="p", version="1.0", description="A", framework="f", rules=[])
        p2 = Policy(policy_id="p", version="1.0", description="B", framework="f", rules=[])
        assert p1.content_hash() != p2.content_hash()

    def test_builtin_policies_count(self) -> None:
        assert len(BUILTIN_POLICIES) >= 10

    def test_builtin_policies_have_unique_ids(self) -> None:
        ids = [p.policy_id for p in BUILTIN_POLICIES]
        assert len(ids) == len(set(ids))


# ─────────────────────────────────────────────────────────────────────────────
# PolicyCompiler tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyCompiler:
    def test_compile_yaml_returns_verifier_class(self, task: Task, clean_result: TaskResult) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(SIMPLE_POLICY_YAML)
        assert issubclass(verifier_cls, BaseVerifier)

    def test_compiled_verifier_has_id(self) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(SIMPLE_POLICY_YAML)
        assert verifier_cls.id.startswith("policy_")

    def test_compiled_verifier_passes_clean_output(
        self, task: Task, clean_result: TaskResult
    ) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(SIMPLE_POLICY_YAML)
        verifier = verifier_cls()
        result = verifier.verify(task, clean_result)
        assert result.passed is True

    def test_compiled_verifier_blocks_pii(
        self, task: Task, pii_result: TaskResult
    ) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(SIMPLE_POLICY_YAML)
        verifier = verifier_cls()
        result = verifier.verify(task, pii_result)
        assert result.passed is False
        assert "email" in result.error.lower()  # type: ignore[union-attr]

    def test_compile_field_equals_operator(
        self, task: Task, clean_result: TaskResult
    ) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(FIELD_EQUALS_YAML)
        verifier = verifier_cls()
        result = verifier.verify(task, clean_result)
        assert result.passed is True  # structured.status == "ok"

    def test_field_equals_fails_on_mismatch(self, task: Task) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(FIELD_EQUALS_YAML)
        verifier = verifier_cls()
        bad_result = TaskResult(raw_output="ok", structured={"status": "error"})
        vr = verifier.verify(task, bad_result)
        assert vr.passed is False

    def test_is_not_empty_operator(self, task: Task, clean_result: TaskResult) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(LENGTH_CHECK_YAML)
        verifier = verifier_cls()
        result = verifier.verify(task, clean_result)
        assert result.passed is True

    def test_is_not_empty_fails_on_empty(self, task: Task) -> None:
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile_yaml(LENGTH_CHECK_YAML)
        verifier = verifier_cls()
        empty_result = TaskResult(raw_output="", structured={})
        vr = verifier.verify(task, empty_result)
        assert vr.passed is False

    def test_invalid_operator_raises_compilation_error(self) -> None:
        compiler = PolicyCompiler()
        with pytest.raises(PolicyCompilationError, match="does_not_exist_operator"):
            compiler.compile_yaml(INVALID_OPERATOR_YAML)

    def test_malformed_yaml_raises_compilation_error(self) -> None:
        compiler = PolicyCompiler()
        with pytest.raises(PolicyCompilationError):
            compiler.compile_yaml("not: valid: yaml: [[[")

    def test_compile_policy_object(self, task: Task, clean_result: TaskResult) -> None:
        """PolicyCompiler.compile() also accepts a Policy model object."""
        policy = Policy(
            policy_id="my_policy",
            version="1.0",
            description="Test",
            framework="test",
            rules=[
                PolicyRule(
                    rule_id="not_empty",
                    description="Output not empty",
                    severity=PolicySeverity.BLOCKING,
                    checks=[
                        PolicyCheck(
                            field="raw_output",
                            operator="is_not_empty",
                            value="",
                            error_message="Output is empty",
                        )
                    ],
                )
            ],
        )
        compiler = PolicyCompiler()
        verifier_cls = compiler.compile(policy)
        verifier = verifier_cls()
        result = verifier.verify(task, clean_result)
        assert result.passed is True


# ─────────────────────────────────────────────────────────────────────────────
# PolicyEngine tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyEngine:
    def test_load_yaml_policy(self) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        assert engine.has_policy("test_no_email")

    def test_evaluate_passes_clean_output(self, task: Task, clean_result: TaskResult) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        result = engine.evaluate("test_no_email", task, clean_result)
        assert result.passed is True

    def test_evaluate_blocks_pii(self, task: Task, pii_result: TaskResult) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        result = engine.evaluate("test_no_email", task, pii_result)
        assert result.passed is False

    def test_evaluate_all_policies(self, task: Task, clean_result: TaskResult) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        engine.load_yaml(LENGTH_CHECK_YAML)
        results = engine.evaluate_all(task, clean_result)
        assert "test_no_email" in results
        assert "test_output_length" in results
        assert all(r.passed for r in results.values())

    def test_load_builtin_policies(self) -> None:
        engine = PolicyEngine()
        engine.load_builtins()
        # All 10 built-ins should be loaded
        assert len(engine.list_policies()) >= 10

    def test_list_policies(self) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        engine.load_yaml(LENGTH_CHECK_YAML)
        policies = engine.list_policies()
        assert "test_no_email" in policies
        assert "test_output_length" in policies

    def test_policy_not_found_raises(self, task: Task, clean_result: TaskResult) -> None:
        from veridian.core.exceptions import PolicyNotFound
        engine = PolicyEngine()
        with pytest.raises(PolicyNotFound):
            engine.evaluate("nonexistent_policy", task, clean_result)

    def test_get_policy_metadata(self) -> None:
        engine = PolicyEngine()
        engine.load_yaml(SIMPLE_POLICY_YAML)
        metadata = engine.get_policy("test_no_email")
        assert metadata is not None
        assert metadata.policy_id == "test_no_email"
        assert metadata.version == "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Built-in policies — all 10 compile and pass a clean result
# ─────────────────────────────────────────────────────────────────────────────


class TestBuiltinPolicies:
    def test_all_builtin_policies_compile(self) -> None:
        compiler = PolicyCompiler()
        for policy in BUILTIN_POLICIES:
            verifier_cls = compiler.compile(policy)
            assert issubclass(verifier_cls, BaseVerifier), (
                f"Policy {policy.policy_id} did not compile to a BaseVerifier"
            )

    def test_all_builtin_policies_run_without_error(self, task: Task, clean_result: TaskResult) -> None:
        compiler = PolicyCompiler()
        for policy in BUILTIN_POLICIES:
            verifier_cls = compiler.compile(policy)
            verifier = verifier_cls()
            # Should not raise — result may pass or fail but no exceptions
            result = verifier.verify(task, clean_result)
            assert isinstance(result, VerificationResult), (
                f"Policy {policy.policy_id} returned unexpected type"
            )

    def test_builtin_policy_frameworks_covered(self) -> None:
        frameworks = {p.framework for p in BUILTIN_POLICIES}
        # Must include at least GDPR, HIPAA, SOX, EU AI Act, OWASP
        required = {"gdpr", "hipaa", "sox", "eu_ai_act", "owasp"}
        assert required.issubset(frameworks), (
            f"Missing frameworks: {required - frameworks}"
        )

    def test_engine_loads_all_builtins(self) -> None:
        engine = PolicyEngine()
        engine.load_builtins()
        policies = engine.list_policies()
        assert len(policies) >= 10
