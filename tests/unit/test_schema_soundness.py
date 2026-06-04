"""
tests.unit.test_schema_soundness
------------------------------------------------------------------------------------------
Permanent regression suite proving the `schema` verifier is SOUND and FAIL-CLOSED.

These pin the defects found by the adversarial audit (tests/audit/test_iter02) as
first-class baseline tests: the headline verifier must enforce the JSON Schema a
user actually writes, recurse into nested structures, honour common keywords, treat
booleans as distinct from integers, and refuse a malformed schema rather than
silently passing (false GREEN is worse than no verifier).

Mapped to BUILD_SPEC_SCHEMA_AND_MATCHERS.md tests T1-T8.
"""

from __future__ import annotations

from typing import Any

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.schema import SchemaVerifier

_TASK = Task(id="t", title="t", verifier_id="schema")


def _verify(schema: dict[str, Any], data: dict[str, Any]) -> Any:
    return SchemaVerifier(schema=schema).verify(_TASK, TaskResult(raw_output="", structured=data))


# ---- T1: nested required ------------------------------------------------------------------


def test_T1_nested_required_is_enforced() -> None:
    schema = {
        "required": ["payload"],
        "properties": {
            "payload": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            }
        },
    }
    assert _verify(schema, {"payload": {"wrong": "x"}}).passed is False


# ---- T2: array items ----------------------------------------------------------------------


def test_T2_array_items_type_is_enforced() -> None:
    schema = {"properties": {"xs": {"type": "array", "items": {"type": "integer"}}}}
    assert _verify(schema, {"xs": [1, "two", 3]}).passed is False


# ---- T3: anyOf ----------------------------------------------------------------------------


def test_T3_anyof_is_enforced() -> None:
    schema = {"properties": {"v": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
    assert _verify(schema, {"v": 1.5}).passed is False


# ---- T4: pattern --------------------------------------------------------------------------


def test_T4_pattern_is_enforced() -> None:
    schema = {"properties": {"country": {"type": "string", "pattern": "^[A-Z]{2}$"}}}
    assert _verify(schema, {"country": "banana"}).passed is False


# ---- T5: malformed schema fails closed ----------------------------------------------------


def test_T5_malformed_schema_raises_config_error() -> None:
    # `required` must be an array of strings; a string is an invalid schema.
    bad_schema = {"required": "not-a-list", "properties": {"a": {"type": "string"}}}
    with pytest.raises(VeridianConfigError):
        SchemaVerifier(schema=bad_schema)


# ---- T6: bool is not integer/number -------------------------------------------------------


def test_T6_bool_does_not_satisfy_integer() -> None:
    assert _verify({"properties": {"n": {"type": "integer"}}}, {"n": True}).passed is False


def test_T6b_bool_does_not_satisfy_number() -> None:
    assert _verify({"properties": {"n": {"type": "number"}}}, {"n": False}).passed is False


# ---- T7: regression - valid data still passes ---------------------------------------------


def test_T7_valid_nested_document_passes() -> None:
    schema = {
        "required": ["payload"],
        "properties": {
            "payload": {
                "type": "object",
                "required": ["id", "tags"],
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "level": {"type": "string", "enum": ["LOW", "HIGH"]},
                },
            }
        },
    }
    data = {"payload": {"id": 7, "tags": ["a", "b"], "level": "HIGH"}}
    assert _verify(schema, data).passed is True


# ---- T8: determinism ----------------------------------------------------------------------


def test_T8_deterministic_across_runs() -> None:
    schema = {"properties": {"country": {"type": "string", "pattern": "^[A-Z]{2}$"}}}
    data = {"country": "banana"}
    results = [_verify(schema, data).evidence.get("field_errors") for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert results[0]  # non-empty error list
