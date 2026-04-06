"""
tests/unit/test_plugin_certification.py
─────────────────────────────────────────
Tests for WCP-028: Plugin Certification — validates plugin behaviour.
"""

from __future__ import annotations

from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.plugins.certification import CertificationResult, CertificationSuite
from veridian.plugins.sdk import VerifierPlugin, plugin_manifest
from veridian.verify.base import VerificationResult

# ── Well-behaved plugin ─────────────────────────────────────────────────────


@plugin_manifest(
    name="good-plugin",
    version="1.0.0",
    author="Test",
    description="Well-behaved plugin",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class GoodPlugin(VerifierPlugin):
    id: ClassVar[str] = "good-plugin"
    description: ClassVar[str] = "Good plugin"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


# ── Plugin that raises bare Exception ──────────────────────────────────────


@plugin_manifest(
    name="bare-exception-plugin",
    version="1.0.0",
    author="Test",
    description="Raises bare Exception",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class BareExceptionPlugin(VerifierPlugin):
    id: ClassVar[str] = "bare-exception-plugin"
    description: ClassVar[str] = "Raises bare Exception"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        raise Exception("bare exception")  # noqa: TRY002


# ── Non-deterministic plugin ───────────────────────────────────────────────

_call_count = 0


@plugin_manifest(
    name="nondeterministic-plugin",
    version="1.0.0",
    author="Test",
    description="Returns different results each call",
    veridian_version_range=">=0.2.0",
    plugin_type="verifier",
)
class NondeterministicPlugin(VerifierPlugin):
    id: ClassVar[str] = "nondeterministic-plugin"
    description: ClassVar[str] = "Nondeterministic"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        global _call_count  # noqa: PLW0603
        _call_count += 1
        return VerificationResult(passed=(_call_count % 2 == 0))


# ── CertificationResult ────────────────────────────────────────────────────


class TestCertificationResult:
    def test_creation(self) -> None:
        r = CertificationResult(
            plugin_name="test",
            passed=True,
            tests_run=5,
            tests_passed=5,
            failures=[],
        )
        assert r.passed is True
        assert r.tests_run == 5
        assert r.tests_passed == 5
        assert r.failures == []

    def test_failed_result(self) -> None:
        r = CertificationResult(
            plugin_name="test",
            passed=False,
            tests_run=3,
            tests_passed=1,
            failures=["bare_exception", "nondeterministic"],
        )
        assert r.passed is False
        assert len(r.failures) == 2


# ── CertificationSuite ─────────────────────────────────────────────────────


class TestCertificationSuite:
    def test_certify_good_plugin_passes(self) -> None:
        suite = CertificationSuite()
        result = suite.certify(GoodPlugin)
        assert result.passed is True
        assert result.tests_passed == result.tests_run
        assert result.failures == []

    def test_certify_bare_exception_fails(self) -> None:
        suite = CertificationSuite()
        result = suite.certify(BareExceptionPlugin)
        assert result.passed is False
        assert any("exception_hierarchy" in f for f in result.failures)

    def test_certify_nondeterministic_fails(self) -> None:
        global _call_count  # noqa: PLW0603
        _call_count = 0
        suite = CertificationSuite()
        result = suite.certify(NondeterministicPlugin)
        assert result.passed is False
        assert any("deterministic" in f for f in result.failures)

    def test_result_contains_details(self) -> None:
        suite = CertificationSuite()
        result = suite.certify(GoodPlugin)
        assert result.plugin_name == "good-plugin"
        assert result.tests_run > 0
