"""
veridian.verify.builtin.any_of
────────────────────────────────
AnyOfVerifier — OR chain: pass if any one sub-verifier passes.

Useful when multiple valid output formats exist (e.g. the agent can
satisfy the task either by creating a file OR returning a URL).

Usage:
    verifier_id="any_of"
    verifier_config={
        "verifiers": [
            {"id": "file_exists", "config": {"files": ["output/report.json"]}},
            {"id": "http_status", "config": {"url": "https://api.example.com/report"}},
        ]
    }
"""

from __future__ import annotations

from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.builtin.composite import _resolve_verifiers


class AnyOfVerifier(BaseVerifier):
    """
    Run all sub-verifiers; pass if at least one passes (OR logic).

    When all fail, the error aggregates individual failure messages
    so the agent knows all attempted verifications failed.
    """

    id: ClassVar[str] = "any_of"
    description: ClassVar[str] = (
        "Pass if any one sub-verifier passes (OR chain). "
        "Error aggregates all individual failures when none pass."
    )

    def __init__(self, verifiers: list[Any]) -> None:
        """
        Args:
            verifiers: List of BaseVerifier instances or dicts {id, config}.
                       Must be non-empty.

        Raises:
            VeridianConfigError: If list is empty.
        """
        if not verifiers:
            raise VeridianConfigError(
                "AnyOfVerifier: 'verifiers' must be a non-empty list. "
                "Provide at least one sub-verifier."
            )
        self.verifiers: list[BaseVerifier] = _resolve_verifiers(verifiers)

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run all sub-verifiers; return pass on first success, else aggregate errors."""
        errors: list[str] = []

        for verifier in self.verifiers:
            sub_result = verifier.verify(task, result)
            if sub_result.passed:
                return VerificationResult(
                    passed=True,
                    evidence={
                        "passing_verifier": verifier.id,
                        "total_verifiers": len(self.verifiers),
                    },
                )
            errors.append(f"{verifier.id}: {sub_result.error or 'failed'}")

        # All verifiers failed — build aggregate error
        joined = "; ".join(errors)
        total = len(self.verifiers)
        error = f"All {total} verifier(s) failed: {joined}"[:300]

        return VerificationResult(
            passed=False,
            error=error,
            evidence={"individual_errors": errors},
        )
