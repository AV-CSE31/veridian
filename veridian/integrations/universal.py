"""
veridian.integrations.universal
────────────────────────────────
Universal Verification Layer — use Veridian with any agent framework.

GAP 2 FIX: Cross-framework integration without lock-in.

Three integration patterns:

  1. UniversalVerifier — standalone verifier callable from any code:
     ```python
     uv = UniversalVerifier(verifiers=["tool_safety", "schema"])
     result = uv.check(code="import os; os.system('rm -rf /')",
                        output={"status": "done"})
     if not result.passed:
         print(result.error)
     ```

  2. VerificationGate — decorator for any function:
     ```python
     @VerificationGate(verifiers=["schema"], required_fields=["answer"])
     def my_agent_step(input: str) -> dict:
         return llm.generate(input)
     ```

  3. Sidecar API (future) — HTTP endpoint for language-agnostic use

Works with: LangGraph, CrewAI, AutoGen, LangChain, custom frameworks.
No restructuring into Task/TaskLedger required.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier
from veridian.verify.builtin.schema import SchemaVerifier
from veridian.verify.builtin.tool_safety import ToolSafetyVerifier

__all__ = ["UniversalVerifier", "VerificationGate"]

log = logging.getLogger(__name__)

# Registry of verifiers available for universal use
_VERIFIER_FACTORY: dict[str, Callable[..., BaseVerifier]] = {
    "tool_safety": lambda **kw: ToolSafetyVerifier(**kw),
    "schema": lambda **kw: SchemaVerifier(**kw),
}


@dataclass
class UniversalResult:
    """Simplified verification result for framework-agnostic use."""

    passed: bool = True
    error: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "error": self.error,
            "details": self.details,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


class UniversalVerifier:
    """Framework-agnostic verification — use from any agent code.

    Wraps Veridian's real verifiers in a simple check() interface.
    No Task/TaskResult/TaskLedger needed. Just pass your data.

    Usage:
        from veridian.integrations import UniversalVerifier

        uv = UniversalVerifier(verifiers=["tool_safety", "schema"],
                               schema_config={"required_fields": ["answer"]})

        # Check agent output
        result = uv.check(
            code="import json\\ndata = json.loads(input)",
            output={"answer": "42", "reasoning": "calculated"},
        )
        if not result.passed:
            raise ValueError(result.error)
    """

    def __init__(
        self,
        verifiers: list[str] | None = None,
        schema_config: dict[str, Any] | None = None,
        tool_safety_config: dict[str, Any] | None = None,
        allow_unknown_verifiers: bool = False,
    ) -> None:
        self._verifiers: list[BaseVerifier] = []
        self._unknown_verifiers: list[str] = []
        self._configured_verifier_ids: list[str] = []
        self._allow_unknown_verifiers = allow_unknown_verifiers
        verifier_ids = ["tool_safety"] if verifiers is None else list(verifiers)
        self._configured_verifier_ids = list(verifier_ids)

        for vid in verifier_ids:
            factory = _VERIFIER_FACTORY.get(vid)
            if factory is None:
                self._unknown_verifiers.append(vid)
                log.warning("Unknown verifier '%s'", vid)
                continue
            if vid == "schema" and schema_config:
                self._verifiers.append(factory(**schema_config))
            elif vid == "tool_safety" and tool_safety_config:
                self._verifiers.append(factory(**tool_safety_config))
            else:
                self._verifiers.append(factory())

    def check(
        self,
        code: str = "",
        output: dict[str, Any] | None = None,
        task_id: str = "universal",
    ) -> UniversalResult:
        """Run all configured verifiers on the provided data.

        Args:
            code: Agent-generated code to verify (for tool_safety)
            output: Structured output dict to verify (for schema)
            task_id: Optional identifier for logging

        Returns:
            UniversalResult with passed/error/details
        """
        start = time.monotonic()

        if self._unknown_verifiers and not self._allow_unknown_verifiers:
            elapsed = (time.monotonic() - start) * 1000
            return UniversalResult(
                passed=False,
                error=(
                    "Unknown verifier(s): "
                    + ", ".join(sorted(self._unknown_verifiers))
                    + ". Set allow_unknown_verifiers=True to skip unknown IDs."
                ),
                details=[
                    {
                        "verifier": vid,
                        "passed": False,
                        "error": "unknown_verifier",
                    }
                    for vid in sorted(self._unknown_verifiers)
                ],
                elapsed_ms=elapsed,
            )

        if not self._verifiers:
            elapsed = (time.monotonic() - start) * 1000
            return UniversalResult(
                passed=False,
                error=(
                    "No active verifiers configured. "
                    f"Configured IDs: {self._configured_verifier_ids!r}"
                ),
                details=[],
                elapsed_ms=elapsed,
            )

        task = Task(id=task_id, title="universal check", verifier_id="universal")

        raw = code or ""
        structured = output or {}
        if code:
            structured["code"] = code

        result = TaskResult(raw_output=raw, structured=structured)
        details: list[dict[str, Any]] = []
        errors: list[str] = []

        for verifier in self._verifiers:
            v = verifier.verify(task, result)
            detail = {
                "verifier": verifier.id,
                "passed": v.passed,
                "error": v.error,
            }
            details.append(detail)
            if not v.passed:
                errors.append(f"[{verifier.id}] {v.error}")

        elapsed = (time.monotonic() - start) * 1000

        if errors:
            return UniversalResult(
                passed=False,
                error="; ".join(errors[:3]),
                details=details,
                elapsed_ms=elapsed,
            )

        return UniversalResult(
            passed=True,
            details=details,
            elapsed_ms=elapsed,
        )


class VerificationGate:
    """Decorator that gates any function with Veridian verification.

    Usage:
        @VerificationGate(verifiers=["schema"],
                          required_fields=["answer", "confidence"])
        def classify(text: str) -> dict:
            return {"answer": "ALLOW", "confidence": 0.95}

        result = classify("test input")
        # If schema check fails, raises VerificationError

    Works with any function in any framework — LangGraph nodes,
    CrewAI tasks, AutoGen agents, custom pipelines.
    """

    def __init__(
        self,
        verifiers: list[str] | None = None,
        required_fields: list[str] | None = None,
        raise_on_fail: bool = True,
    ) -> None:
        schema_config = {}
        if required_fields:
            schema_config["required_fields"] = required_fields

        self._uv = UniversalVerifier(
            verifiers=verifiers or ["schema"],
            schema_config=schema_config or None,
        )
        self._raise = raise_on_fail

    def __call__(self, fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = fn(*args, **kwargs)

            # Determine if result contains code
            code = result.get("code", "") if isinstance(result, dict) else ""
            output = result if isinstance(result, dict) else {}

            check = self._uv.check(code=code, output=output)

            if not check.passed and self._raise:
                from veridian.core.exceptions import VerificationError

                raise VerificationError(f"VerificationGate failed: {check.error}")

            return result

        return wrapper
