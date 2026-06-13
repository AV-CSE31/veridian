"""
ADVERSARIAL AUDIT — Iteration 2: Verifier soundness.

The product's headline is "deterministic verification" and the README's headline
verifier is `schema` (JSON Schema). These tests assert the schema verifier
enforces the schema a user actually writes. They FAIL because the implementation
is a non-recursive subset that silently drops constraints — returning passed=True
on data that violates the declared schema. Silent false-GREEN is worse than no
verifier: it manufactures confidence.

  I2-1 (P1): nested object/array constraints are not validated.
  I2-2 (P1): common JSON Schema keywords (pattern, anyOf, additionalProperties)
             are silently ignored.
  I2-3 (P2): bool passes `type: integer` (isinstance(True, int) is True).
"""

from __future__ import annotations

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.schema import SchemaVerifier

_TASK = Task(id="t", title="t", verifier_id="schema")


def _verify(schema: dict, data: dict):
    v = SchemaVerifier(schema=schema)
    return v.verify(_TASK, TaskResult(raw_output="", structured=data))


def test_I2_1_nested_object_constraints_are_enforced() -> None:
    """A nested object's own `required`/`type` must be enforced. The agent
    returns a payload missing its required nested field; the verifier must fail.
    """
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
    # payload is an object (top-level type ok) but missing required nested 'id',
    # and 'wrong' is a string where the schema declares nothing valid.
    res = _verify(schema, {"payload": {"wrong": "x"}})
    assert res.passed is False, (
        "Nested object schema NOT enforced: payload missing required 'id' passed. "
        "The verifier only validates the top level; every nested constraint a "
        "user writes is silently ignored — false GREEN on structured output."
    )


def test_I2_2_pattern_keyword_is_enforced() -> None:
    """`pattern` (regex) is one of the most common JSON Schema constraints —
    invoice IDs, SKUs, country codes. A value violating the pattern must fail.
    """
    schema = {
        "properties": {
            "country": {"type": "string", "pattern": "^[A-Z]{2}$"},
        }
    }
    res = _verify(schema, {"country": "banana"})
    assert res.passed is False, (
        "`pattern` silently ignored: 'banana' passed a ^[A-Z]{2}$ constraint. "
        "The verifier advertises JSON Schema but implements an undocumented "
        "subset; any unsupported keyword is dropped without warning."
    )


def test_I2_3_boolean_does_not_satisfy_integer_type() -> None:
    """JSON Schema treats boolean and integer as distinct types. Python's
    isinstance(True, int) is True, so the naive check accepts a bool where an
    integer is required.
    """
    schema = {"properties": {"count": {"type": "integer"}}}
    res = _verify(schema, {"count": True})
    assert res.passed is False, (
        "bool accepted as integer: schema type-checking uses isinstance, so "
        "True/False pass `type: integer` and `type: number`. A count field that "
        "should hold 0..N silently accepts a boolean."
    )
