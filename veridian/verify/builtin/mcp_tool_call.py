"""
veridian.verify.builtin.mcp_tool_call
──────────────────────────────────────
MCPToolCallVerifier — validate the output of an MCP tool call against a
declared contract.

Usage:
    verifier_config={
        "tool_name": "read_file",
        "expected_keys": ["content", "path"],
        "forbidden_keys": ["error"],
        "schema": {
            "properties": {
                "content": {"type": "string"},
            }
        },
        "allow_error": False,
    }

The verifier inspects ``result.raw_output`` (JSON string) or
``result.structured`` (already-parsed dict) for an MCP tool call result.
Precedence: ``structured`` if non-empty, else parse ``raw_output``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.builtin.schema import _check_json_schema

log = logging.getLogger(__name__)


class MCPToolCallVerifier(BaseVerifier):
    """
    Validate MCP tool call results against a declared contract.

    Checks (in order):
    1. Result is parseable as a dict.
    2. If ``expected_keys`` is set, all keys are present.
    3. If ``forbidden_keys`` is set, none are present.
    4. If ``allow_error=False`` (default), the ``"error"`` key must not exist.
    5. If ``schema`` is set, validate against it (JSON Schema subset).
    """

    id: ClassVar[str] = "mcp_tool_call"
    description: ClassVar[str] = (
        "Validates MCP tool call result dicts against expected/forbidden keys, "
        "an optional JSON Schema, and error-key presence."
    )

    def __init__(
        self,
        tool_name: str,
        expected_keys: list[str] | None = None,
        forbidden_keys: list[str] | None = None,
        schema: dict[str, Any] | None = None,
        allow_error: bool = False,
    ) -> None:
        """
        Args:
            tool_name: Expected MCP tool name (stored in evidence; used for
                       labelling — MCP results do not embed a tool_name field
                       by convention, so this is declarative only).
            expected_keys: Keys that MUST be present in the result dict.
            forbidden_keys: Keys that MUST NOT be present in the result dict.
            schema: Optional JSON Schema dict for structural validation.
                    Uses the same built-in checker as SchemaVerifier — no
                    external dependencies required.
            allow_error: If False (default), the result must not contain a
                         top-level ``"error"`` key.
        """
        if not tool_name or not tool_name.strip():
            from veridian.core.exceptions import VeridianConfigError

            raise VeridianConfigError(
                "MCPToolCallVerifier: 'tool_name' must be a non-empty string."
            )
        self.tool_name = tool_name.strip()
        self.expected_keys: list[str] = list(expected_keys or [])
        self.forbidden_keys: list[str] = list(forbidden_keys or [])
        self.schema = schema
        self.allow_error = allow_error

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_result(self, task_result: TaskResult) -> dict[str, Any] | str:
        """
        Return parsed dict from TaskResult, or an error string on failure.

        Priority:
        - ``result.structured`` if non-empty (already parsed).
        - ``result.raw_output`` parsed as JSON.
        """
        if task_result.structured:
            return task_result.structured

        raw = task_result.raw_output.strip()
        if not raw:
            return "MCP tool call result is empty — expected a JSON object"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return f"MCP tool call result is not valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return (
                f"MCP tool call result must be a JSON object (dict), got {type(parsed).__name__!r}"
            )
        return parsed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Validate MCP tool call result against the configured contract."""
        # Step 1: parse
        parsed = self._parse_result(result)
        if isinstance(parsed, str):
            return VerificationResult(
                passed=False,
                error=parsed[:300],
                evidence={"tool_name": self.tool_name},
            )

        data: dict[str, Any] = parsed
        errors: list[str] = []

        # Step 2: expected_keys
        for key in self.expected_keys:
            if key not in data:
                errors.append(f"expected key '{key}' is missing from MCP result")

        # Step 3: forbidden_keys
        for key in self.forbidden_keys:
            if key in data:
                errors.append(f"forbidden key '{key}' is present in MCP result")

        # Step 4: error key check
        if not self.allow_error and "error" in data:
            errors.append(
                f"MCP result contains 'error' key (value={data['error']!r}); "
                f"set allow_error=True to permit error responses"
            )

        # Step 5: JSON Schema validation (built-in, no external deps)
        if self.schema is not None and isinstance(self.schema, dict):
            schema_errors = _check_json_schema(self.schema, data)
            errors.extend(schema_errors)

        if not errors:
            return VerificationResult(
                passed=True,
                evidence={
                    "tool_name": self.tool_name,
                    "keys_checked": sorted(data.keys()),
                    "expected_keys": self.expected_keys,
                    "forbidden_keys": self.forbidden_keys,
                    "schema_validated": self.schema is not None,
                },
            )

        unique_errors = list(dict.fromkeys(errors))
        summary = "; ".join(unique_errors[:3])
        error_msg = f"MCP tool call '{self.tool_name}' failed: {summary}"[:300]

        return VerificationResult(
            passed=False,
            error=error_msg,
            evidence={
                "tool_name": self.tool_name,
                "errors": unique_errors,
                "keys_present": sorted(data.keys()),
            },
        )
