"""
veridian.verify.builtin.composite
──────────────────────────────────
CompositeVerifier — AND chain: run all verifiers in order, stop on first failure.

Errors are prefixed with "[Step N/total] verifier_id: ..." to pinpoint
which step failed and give the agent a clear fix target.

RULE: LLMJudgeVerifier may NOT be the only verifier in a composite chain.
      LLM judgment is probabilistic; it must be gated by at least one deterministic check.

Usage:
    verifier_id="composite"
    verifier_config={
        "verifiers": [
            {"id": "schema",     "config": {"required_fields": ["quote", "risk_level"]}},
            {"id": "quote_match","config": {"source_file": "contracts/001.pdf"}},
        ]
    }
"""

from __future__ import annotations

from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


def _resolve_verifiers(items: list[Any]) -> list[BaseVerifier]:
    """
    Accept a list of BaseVerifier instances OR dicts {"id": ..., "config": {...}}.
    Returns a list of instantiated BaseVerifier objects.
    """
    resolved: list[BaseVerifier] = []
    for item in items:
        if isinstance(item, BaseVerifier):
            resolved.append(item)
        elif isinstance(item, dict):
            from veridian.verify.base import registry  # noqa: PLC0415

            verifier_id: str = item["id"]
            config: dict[str, Any] | None = item.get("config")
            resolved.append(registry.get(verifier_id, config))
        else:
            raise VeridianConfigError(
                f"CompositeVerifier: each verifier must be a BaseVerifier instance or a dict "
                f"with 'id' key. Got {type(item).__name__}."
            )
    return resolved


class CompositeVerifier(BaseVerifier):
    """
    Run all sub-verifiers in order (AND chain).

    Stops at the first failure and prefixes the error with the step number.
    All sub-verifiers must pass for composite to pass.
    """

    id: ClassVar[str] = "composite"
    description: ClassVar[str] = (
        "Run all sub-verifiers in order. Fail on first failure. "
        "Errors prefixed with '[Step N/total]' to identify the failing step."
    )

    def __init__(self, verifiers: list[Any]) -> None:
        """
        Args:
            verifiers: List of BaseVerifier instances or dicts {id, config}.
                       Must be non-empty.

        Raises:
            VeridianConfigError: If list is empty or contains only LLMJudgeVerifier.
        """
        if not verifiers:
            raise VeridianConfigError(
                "CompositeVerifier: 'verifiers' must be a non-empty list. "
                "Provide at least one sub-verifier."
            )
        self.verifiers: list[BaseVerifier] = _resolve_verifiers(verifiers)

        # Guard: LLMJudgeVerifier cannot be the only verifier
        if len(self.verifiers) == 1 and self.verifiers[0].id == "llm_judge":
            raise VeridianConfigError(
                "LLMJudgeVerifier cannot run standalone. "
                "Wrap it with at least one deterministic verifier in CompositeVerifier."
            )

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run sub-verifiers in order. Return on first failure."""
        total = len(self.verifiers)

        for i, verifier in enumerate(self.verifiers, start=1):
            sub_result = verifier.verify(task, result)
            if not sub_result.passed:
                prefix = f"[Step {i}/{total}] {verifier.id}: "
                raw_error = sub_result.error or "verification failed"
                # Combine prefix with error, truncated to 300 chars
                error = (prefix + raw_error)[:300]
                return VerificationResult(
                    passed=False,
                    error=error,
                    evidence={
                        "failed_step": i,
                        "failed_verifier": verifier.id,
                        "sub_error": sub_result.error,
                    },
                )

        return VerificationResult(
            passed=True,
            evidence={
                "steps_passed": total,
                "verifiers": [v.id for v in self.verifiers],
            },
        )
