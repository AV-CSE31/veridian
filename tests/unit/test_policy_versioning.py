"""
tests.unit.test_policy_versioning
─────────────────────────────────
Acceptance tests for the policy-versioning contract:

* Every PolicyEngine.evaluate() result stamps policy_id, policy_version,
  and policy_hash into the evidence dict.
* is_evidence_current() recognises evidence produced by the registered
  version of a policy.
* A new policy version (or a rule edit that changes content_hash) marks
  prior evidence as stale — the new policy must fail old evidence by
  default unless re-verified.
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import PolicyNotFound
from veridian.core.task import Task, TaskResult
from veridian.policy.engine import PolicyEngine
from veridian.policy.models import Policy, PolicyCheck, PolicyRule, PolicySeverity


def _mk_policy(version: str, value: str = "ERROR") -> Policy:
    return Policy(
        policy_id="test_no_errors",
        version=version,
        description="Output must not contain ERROR",
        framework="test",
        rules=[
            PolicyRule(
                rule_id="no_errors",
                description="raw_output must not contain the literal pattern",
                severity=PolicySeverity.BLOCKING,
                checks=[
                    PolicyCheck(
                        field="raw_output",
                        operator="not_contains_pattern",
                        value=value,
                        error_message="found forbidden token",
                    )
                ],
            )
        ],
    )


@pytest.fixture
def task() -> Task:
    return Task(id="t1", title="t", description="d")


@pytest.fixture
def clean_result() -> TaskResult:
    return TaskResult(raw_output="all good")


@pytest.fixture
def engine_v1() -> PolicyEngine:
    engine = PolicyEngine()
    compiled = engine._compiler.compile(_mk_policy("1.0.0"))
    engine._registry["test_no_errors"] = (_mk_policy("1.0.0"), compiled)
    return engine


class TestEvidenceStamping:
    def test_evaluate_stamps_policy_id_version_and_hash(
        self, engine_v1: PolicyEngine, task: Task, clean_result: TaskResult
    ) -> None:
        result = engine_v1.evaluate("test_no_errors", task, clean_result)
        assert result.passed
        assert result.evidence["policy_id"] == "test_no_errors"
        assert result.evidence["policy_version"] == "1.0.0"
        assert isinstance(result.evidence["policy_hash"], str)
        assert len(result.evidence["policy_hash"]) == 64  # SHA-256 hex

    def test_failed_evaluation_still_stamps_provenance(
        self, engine_v1: PolicyEngine, task: Task
    ) -> None:
        bad = TaskResult(raw_output="ERROR: boom")
        result = engine_v1.evaluate("test_no_errors", task, bad)
        assert not result.passed
        assert result.evidence["policy_id"] == "test_no_errors"
        assert result.evidence["policy_version"] == "1.0.0"


class TestEvidenceCurrencyCheck:
    def test_fresh_evidence_is_current(
        self, engine_v1: PolicyEngine, task: Task, clean_result: TaskResult
    ) -> None:
        result = engine_v1.evaluate("test_no_errors", task, clean_result)
        assert engine_v1.is_evidence_current("test_no_errors", dict(result.evidence))

    def test_old_evidence_is_stale_after_rule_change(
        self, engine_v1: PolicyEngine, task: Task, clean_result: TaskResult
    ) -> None:
        # Generate evidence under v1.0.0
        old_result = engine_v1.evaluate("test_no_errors", task, clean_result)
        old_evidence = dict(old_result.evidence)

        # Upgrade policy to v2.0.0 with a rule change (new forbidden token)
        engine_v2 = PolicyEngine()
        v2 = _mk_policy("2.0.0", value="FATAL")
        compiled = engine_v2._compiler.compile(v2)
        engine_v2._registry["test_no_errors"] = (v2, compiled)

        # Old evidence must be flagged as stale under the new policy.
        assert not engine_v2.is_evidence_current("test_no_errors", old_evidence)

    def test_version_bump_alone_marks_evidence_stale(
        self, engine_v1: PolicyEngine, task: Task, clean_result: TaskResult
    ) -> None:
        # Generate evidence under v1.0.0
        old_result = engine_v1.evaluate("test_no_errors", task, clean_result)
        old_evidence = dict(old_result.evidence)

        # Bump only the version field — content_hash() includes version, so
        # the hash changes. This enforces the "fail closed" stance: any
        # version edit invalidates prior evidence and forces re-verification.
        engine_v1b = PolicyEngine()
        v1b = _mk_policy("1.0.1")
        compiled = engine_v1b._compiler.compile(v1b)
        engine_v1b._registry["test_no_errors"] = (v1b, compiled)

        assert not engine_v1b.is_evidence_current("test_no_errors", old_evidence)

    def test_evidence_without_provenance_is_not_current(self, engine_v1: PolicyEngine) -> None:
        # Evidence dicts from older runtimes won't have the stamps. These
        # must not be silently treated as fresh.
        assert not engine_v1.is_evidence_current("test_no_errors", {})

    def test_is_evidence_current_unknown_policy_raises(self, engine_v1: PolicyEngine) -> None:
        with pytest.raises(PolicyNotFound):
            engine_v1.is_evidence_current("nonexistent", {})
