"""Decorator API for verifying ordinary Python function outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec

from veridian import __version__
from veridian.core.exceptions import VerificationError
from veridian.core.report import VerificationReport, append_report_jsonl
from veridian.core.task import Task, TaskResult
from veridian.verify.base import VerificationResult, VerifierRegistry, registry

P = ParamSpec("P")


@dataclass(frozen=True)
class VerifiedCall:
    """Result returned by a function wrapped with ``@verified``."""

    value: Any
    task: Task
    result: TaskResult
    verification: VerificationResult
    report: VerificationReport

    @property
    def passed(self) -> bool:
        """Return whether the wrapped function output passed verification."""
        return self.verification.passed

    @property
    def error(self) -> str | None:
        """Return the verifier error when verification failed."""
        return self.verification.error

    @property
    def structured(self) -> dict[str, Any]:
        """Return the structured payload sent to the verifier."""
        return self.result.structured

    def raise_for_failure(self) -> VerifiedCall:
        """Raise ``VerificationError`` when the verifier rejected the output."""
        if not self.passed:
            raise VerificationError(self.error or "Verification failed")
        return self


def _as_structured(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def verified(
    verifier_id: str = "schema",
    verifier_config: dict[str, Any] | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    strict: bool = False,
    verifier_registry: VerifierRegistry | None = None,
    report_file: str | Path | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, VerifiedCall]]:
    """Verify a function's return value with a Veridian verifier.

    The wrapped function returns ``VerifiedCall`` instead of the raw value.
    Set ``strict=True`` to raise ``VerificationError`` on failed verification.
    """

    def decorate(fn: Callable[P, Any]) -> Callable[P, VerifiedCall]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> VerifiedCall:
            value = fn(*args, **kwargs)
            task = Task(
                title=title or fn.__name__.replace("_", " "),
                description=description or (fn.__doc__ or "").strip(),
                verifier_id=verifier_id,
                verifier_config=dict(verifier_config or {}),
            )
            result = TaskResult(raw_output=repr(value), structured=_as_structured(value))
            selected_registry = verifier_registry or registry
            verifier = selected_registry.get(verifier_id, verifier_config or None)
            verification = verifier.verify(task, result)
            result.verified = verification.passed
            result.verification_error = verification.error
            result.verification_evidence = verification.evidence
            result.verifier_score = verification.score
            report = VerificationReport.from_task_result(
                task=task,
                result=result,
                passed=verification.passed,
                error=verification.error,
                evidence=verification.evidence or {},
                score=verification.score,
                runtime_version=__version__,
                metadata={"source": "decorator", "function": fn.__name__},
            )
            if report_file is not None:
                report = append_report_jsonl(report_file, report)
            result.verification_report = report.to_dict()

            call = VerifiedCall(
                value=value,
                task=task,
                result=result,
                verification=verification,
                report=report,
            )
            if strict:
                call.raise_for_failure()
            return call

        return wrapper

    return decorate
