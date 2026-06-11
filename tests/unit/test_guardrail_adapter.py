"""
tests.unit.test_guardrail_adapter
---------------------------------------------------------------------
BaseVerifier.as_guardrail() --- the framework-agnostic adapter that lets a
Veridian verifier run as a CrewAI-style function guardrail or inside a
LangGraph node without Veridian owning the execution loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from veridian.verify.base import registry

CONTRACT: dict[str, Any] = {
    "required": ["status"],
    "properties": {"status": {"type": "string", "enum": ["complete"]}},
}


def _schema_guardrail():
    return registry.get("schema", {"schema": CONTRACT}).as_guardrail()


class TestGuardrailContract:
    def test_dict_output_passes_and_is_returned_unchanged(self) -> None:
        guardrail = _schema_guardrail()
        output = {"status": "complete"}
        passed, payload = guardrail(output)
        assert passed is True
        assert payload is output

    def test_dict_output_failing_contract_returns_error_string(self) -> None:
        guardrail = _schema_guardrail()
        passed, payload = guardrail({"status": "in_progress"})
        assert passed is False
        assert isinstance(payload, str) and payload

    def test_missing_required_field_fails(self) -> None:
        guardrail = _schema_guardrail()
        passed, _ = guardrail({"other": 1})
        assert passed is False

    def test_crewai_style_task_output_object_is_unwrapped(self) -> None:
        @dataclass
        class FakeTaskOutput:
            raw: str
            json_dict: dict[str, Any]

        guardrail = _schema_guardrail()
        good = FakeTaskOutput(raw="done", json_dict={"status": "complete"})
        passed, payload = guardrail(good)
        assert passed is True
        assert payload is good

        bad = FakeTaskOutput(raw="done", json_dict={"status": "nope"})
        passed, payload = guardrail(bad)
        assert passed is False
        assert isinstance(payload, str)

    def test_string_output_becomes_raw_output(self) -> None:
        from veridian.core.task import Task, TaskResult
        from veridian.verify.base import BaseVerifier, VerificationResult

        class MarkerVerifier(BaseVerifier):
            id = "test_marker"

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                if "all tests pass" in result.raw_output:
                    return VerificationResult(passed=True)
                return VerificationResult(passed=False, error="marker missing")

        guardrail = MarkerVerifier().as_guardrail()
        passed, payload = guardrail("Summary: all tests pass in CI.")
        assert passed is True
        assert payload == "Summary: all tests pass in CI."

        passed, payload = guardrail("Summary: build still red.")
        assert passed is False
        assert payload == "marker missing"

    def test_non_str_non_dict_output_is_serialized(self) -> None:
        guardrail = _schema_guardrail()
        # A list has no structured dict, so the schema contract must fail
        # cleanly rather than raise.
        passed, payload = guardrail([1, 2, 3])
        assert passed is False
        assert isinstance(payload, str)
