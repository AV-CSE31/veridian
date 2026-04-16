"""
tests.unit.test_mcp_tool_call
──────────────────────────────
MCPToolCallVerifier — validate MCP tool call result dicts against a
declared contract (expected_keys, forbidden_keys, schema, allow_error).
"""

from __future__ import annotations

import json

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.mcp_tool_call import MCPToolCallVerifier

# ─── helpers ──────────────────────────────────────────────────────────────────


def make_task() -> Task:
    return Task(title="mcp_test")


def make_result_from_dict(data: dict) -> TaskResult:
    """Build a TaskResult with pre-parsed structured output."""
    return TaskResult(raw_output="", structured=data)


def make_result_from_json(data: dict) -> TaskResult:
    """Build a TaskResult with JSON-encoded raw_output and empty structured."""
    return TaskResult(raw_output=json.dumps(data), structured={})


def make_result_raw(raw: str) -> TaskResult:
    """Build a TaskResult with arbitrary raw_output."""
    return TaskResult(raw_output=raw, structured={})


# ══════════════════════════════════════════════════════════════════════════════
# Init / config validation
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPToolCallVerifierInit:
    def test_valid_construction(self) -> None:
        v = MCPToolCallVerifier(tool_name="read_file")
        assert v.tool_name == "read_file"
        assert v.expected_keys == []
        assert v.forbidden_keys == []
        assert v.schema is None
        assert v.allow_error is False

    def test_id_is_mcp_tool_call(self) -> None:
        v = MCPToolCallVerifier(tool_name="list_dir")
        assert v.id == "mcp_tool_call"

    def test_empty_tool_name_raises(self) -> None:
        with pytest.raises(VeridianConfigError, match="tool_name"):
            MCPToolCallVerifier(tool_name="")

    def test_whitespace_tool_name_raises(self) -> None:
        with pytest.raises(VeridianConfigError, match="tool_name"):
            MCPToolCallVerifier(tool_name="   ")

    def test_tool_name_stripped(self) -> None:
        v = MCPToolCallVerifier(tool_name="  read_file  ")
        assert v.tool_name == "read_file"


# ══════════════════════════════════════════════════════════════════════════════
# Happy-path (passing) cases
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPToolCallVerifierPass:
    def test_valid_result_with_expected_keys_passes(self) -> None:
        """Result containing all expected_keys should pass."""
        v = MCPToolCallVerifier(
            tool_name="read_file",
            expected_keys=["content", "path"],
        )
        result = make_result_from_dict({"content": "hello", "path": "/tmp/f.txt"})
        vr = v.verify(make_task(), result)
        assert vr.passed is True

    def test_result_from_json_string_passes(self) -> None:
        """Verifier should parse raw_output JSON when structured is empty."""
        v = MCPToolCallVerifier(tool_name="list_dir", expected_keys=["files"])
        result = make_result_from_json({"files": ["a.py", "b.py"]})
        vr = v.verify(make_task(), result)
        assert vr.passed is True

    def test_no_constraints_passes(self) -> None:
        """With no expected_keys/forbidden_keys/schema, any dict should pass."""
        v = MCPToolCallVerifier(tool_name="ping")
        result = make_result_from_dict({"status": "ok"})
        vr = v.verify(make_task(), result)
        assert vr.passed is True

    def test_error_key_allowed_when_allow_error_true(self) -> None:
        """Result with 'error' key should pass when allow_error=True."""
        v = MCPToolCallVerifier(tool_name="read_file", allow_error=True)
        result = make_result_from_dict({"error": "file not found"})
        vr = v.verify(make_task(), result)
        assert vr.passed is True

    def test_schema_validation_passes(self) -> None:
        """Result conforming to the declared JSON Schema should pass."""
        v = MCPToolCallVerifier(
            tool_name="get_user",
            schema={
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        )
        result = make_result_from_dict({"id": 42, "name": "Alice"})
        vr = v.verify(make_task(), result)
        assert vr.passed is True

    def test_evidence_contains_tool_name(self) -> None:
        """Passing result evidence should include tool_name."""
        v = MCPToolCallVerifier(tool_name="my_tool")
        result = make_result_from_dict({"ok": True})
        vr = v.verify(make_task(), result)
        assert vr.passed is True
        assert vr.evidence.get("tool_name") == "my_tool"


# ══════════════════════════════════════════════════════════════════════════════
# Failure cases
# ══════════════════════════════════════════════════════════════════════════════


class TestMCPToolCallVerifierFail:
    def test_missing_expected_key_fails(self) -> None:
        """Missing expected key should cause failure with informative reason."""
        v = MCPToolCallVerifier(
            tool_name="read_file",
            expected_keys=["content", "path"],
        )
        result = make_result_from_dict({"content": "hello"})  # missing "path"
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "path" in vr.error

    def test_forbidden_key_present_fails(self) -> None:
        """Presence of a forbidden key should cause failure."""
        v = MCPToolCallVerifier(
            tool_name="read_file",
            forbidden_keys=["__internal__"],
        )
        result = make_result_from_dict({"content": "hi", "__internal__": "secret"})
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "__internal__" in vr.error

    def test_error_key_fails_when_allow_error_false(self) -> None:
        """Result with 'error' key should fail when allow_error=False (default)."""
        v = MCPToolCallVerifier(tool_name="read_file")
        result = make_result_from_dict({"error": "permission denied"})
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "error" in vr.error.lower()

    def test_invalid_json_string_fails_gracefully(self) -> None:
        """Non-JSON raw_output should fail with an informative error, not crash."""
        v = MCPToolCallVerifier(tool_name="read_file")
        result = make_result_raw("this is not json {{{")
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "json" in vr.error.lower()

    def test_empty_raw_output_fails_gracefully(self) -> None:
        """Empty raw_output with no structured data should fail with clear error."""
        v = MCPToolCallVerifier(tool_name="read_file")
        result = make_result_raw("")
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None

    def test_non_dict_json_fails(self) -> None:
        """JSON array (not object) should fail with clear type error."""
        v = MCPToolCallVerifier(tool_name="read_file")
        result = make_result_raw(json.dumps(["item1", "item2"]))
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "dict" in vr.error or "object" in vr.error

    def test_schema_validation_fails_on_wrong_type(self) -> None:
        """Result violating the JSON Schema type constraint should fail."""
        v = MCPToolCallVerifier(
            tool_name="get_user",
            schema={
                "properties": {
                    "age": {"type": "integer"},
                },
            },
        )
        result = make_result_from_dict({"age": "not-a-number"})
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "age" in vr.error

    def test_schema_missing_required_field_fails(self) -> None:
        """Result missing a JSON Schema required field should fail."""
        v = MCPToolCallVerifier(
            tool_name="get_item",
            schema={"required": ["id", "name"]},
        )
        result = make_result_from_dict({"id": 1})  # missing "name"
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert "name" in vr.error

    def test_error_message_length_within_limit(self) -> None:
        """Error message must be ≤ 300 chars (CLAUDE.md §RULES)."""
        v = MCPToolCallVerifier(
            tool_name="read_file",
            expected_keys=[f"key_{i}" for i in range(20)],
        )
        result = make_result_from_dict({})
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.error is not None
        assert len(vr.error) <= 300

    def test_failure_evidence_contains_tool_name(self) -> None:
        """Failing result evidence should still report the tool_name."""
        v = MCPToolCallVerifier(tool_name="broken_tool", expected_keys=["x"])
        result = make_result_from_dict({"y": 1})
        vr = v.verify(make_task(), result)
        assert vr.passed is False
        assert vr.evidence.get("tool_name") == "broken_tool"
