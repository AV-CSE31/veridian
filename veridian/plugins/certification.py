"""
veridian.plugins.certification
──────────────────────────────
Plugin certification checks for verifier and hook plugins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from veridian.core.exceptions import VeridianError
from veridian.core.task import Task, TaskResult
from veridian.plugins.sdk import HookPlugin, VerifierPlugin

__all__ = ["CertificationResult", "CertificationSuite"]

PluginT = TypeVar("PluginT", bound=VerifierPlugin | HookPlugin)


@dataclass(frozen=True)
class CertificationResult:
    """Result of running certification checks for one plugin."""

    plugin_name: str
    passed: bool
    tests_run: int
    tests_passed: int
    failures: list[str] = field(default_factory=list)


class CertificationSuite:
    """Runs a deterministic set of plugin certification checks."""

    def certify(self, plugin_cls: type[PluginT]) -> CertificationResult:
        failures: list[str] = []
        tests_run = 0
        tests_passed = 0
        plugin_name = getattr(
            getattr(plugin_cls, "plugin_metadata", None), "name", plugin_cls.__name__
        )

        tests_run += 1
        if hasattr(plugin_cls, "plugin_metadata"):
            tests_passed += 1
        else:
            failures.append("metadata: missing plugin_metadata")

        tests_run += 1
        try:
            plugin = plugin_cls()
            tests_passed += 1
        except Exception as exc:
            failures.append(f"construct: {type(exc).__name__}: {exc}")
            return CertificationResult(
                plugin_name=plugin_name,
                passed=False,
                tests_run=tests_run,
                tests_passed=tests_passed,
                failures=failures,
            )

        if isinstance(plugin, VerifierPlugin):
            verifier_checks = self._check_verifier(plugin)
            tests_run += verifier_checks["tests_run"]
            tests_passed += verifier_checks["tests_passed"]
            failures.extend(verifier_checks["failures"])

        passed = len(failures) == 0
        return CertificationResult(
            plugin_name=plugin_name,
            passed=passed,
            tests_run=tests_run,
            tests_passed=tests_passed,
            failures=failures,
        )

    def _check_verifier(self, plugin: VerifierPlugin) -> dict[str, Any]:
        failures: list[str] = []
        tests_run = 0
        tests_passed = 0

        task = Task(
            title="certification task",
            description="certification",
            verifier_id=plugin.id or "plugin",
        )
        result = TaskResult(raw_output="certification output")

        # Check 1: Exception hierarchy / safe error behavior.
        tests_run += 1
        first_outcome: tuple[bool, str | None, dict[str, Any]] | None = None
        try:
            first = plugin.verify(task, result)
            first_outcome = (bool(first.passed), first.error, dict(first.evidence))
            tests_passed += 1
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, VeridianError):
                tests_passed += 1
            else:
                failures.append("exception_hierarchy: verifier raised bare Exception")

        # Check 2: Determinism for same inputs.
        tests_run += 1
        if first_outcome is None:
            failures.append("deterministic: unable to evaluate (first invocation failed)")
            return {"tests_run": tests_run, "tests_passed": tests_passed, "failures": failures}

        try:
            second = plugin.verify(task, result)
            second_outcome = (bool(second.passed), second.error, dict(second.evidence))
            if second_outcome == first_outcome:
                tests_passed += 1
            else:
                failures.append("deterministic: repeated invocation returned different output")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, VeridianError):
                tests_passed += 1
            else:
                failures.append("deterministic: second invocation raised bare Exception")

        return {"tests_run": tests_run, "tests_passed": tests_passed, "failures": failures}
