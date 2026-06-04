"""
veridian.verify.builtin.schema
------------------------------------------------------------------------------------------
SchemaVerifier --- validate result.structured against a JSON Schema dict
or a Pydantic model (referenced as "module.path:ClassName").

Usage:
    # required_fields only:
    verifier_config={"required_fields": ["quote", "risk_level", "page_number"]}

    # JSON Schema dict:
    verifier_config={
        "schema": {
            "required": ["risk_level"],
            "properties": {
                "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]}
            }
        }
    }

    # Pydantic model path:
    verifier_config={"schema": "my_package.models:ClauseResult"}
"""

from __future__ import annotations

import importlib
from typing import Any, ClassVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


def _validate_schema_or_raise(schema: dict[str, Any]) -> None:
    """Fail-closed schema check. A malformed JSON Schema is a configuration error,
    not a verification outcome --- raise so it surfaces at setup instead of silently
    passing every payload (false GREEN).
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise VeridianConfigError(
            f"Invalid JSON Schema supplied to SchemaVerifier: {exc.message}"
        ) from exc


def _check_json_schema(schema: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """
    Sound JSON Schema (Draft 2020-12) validation via the ``jsonschema`` library.

    Recurses into nested objects/arrays, honours ``pattern``/``anyOf``/``allOf``/
    ``oneOf``/``items``/nested ``required``, and treats booleans as distinct from
    integers/numbers. Returns a deterministic, sorted list of error strings.
    Assumes the schema was already validated by ``_validate_schema_or_raise``.
    """
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in validator.iter_errors(data):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{location}: {err.message}")
    # Deterministic ordering independent of dict/iteration order.
    return sorted(errors)


def _validate_pydantic(model_path: str, data: dict[str, Any]) -> list[str]:
    """
    Import Pydantic model from 'module.path:ClassName' and validate data.
    Returns list of field-level error strings.
    """
    try:
        module_str, class_str = model_path.rsplit(":", 1)
    except ValueError:
        return [f"Invalid Pydantic model path '{model_path}'. Use 'module.path:ClassName'."]

    try:
        module = importlib.import_module(module_str)
    except ImportError as exc:
        return [f"Cannot import module '{module_str}': {exc}"]

    cls = getattr(module, class_str, None)
    if cls is None:
        return [f"Class '{class_str}' not found in module '{module_str}'"]

    try:
        from pydantic import ValidationError  # noqa: PLC0415,F401

        cls(**data)
        return []
    except ImportError:
        return ["pydantic is required for model path validation"]
    except Exception as exc:
        # Pydantic ValidationError has .errors()
        if hasattr(exc, "errors"):
            return [f"{e['loc']}: {e['msg']}" for e in exc.errors()]
        return [str(exc)]


class SchemaVerifier(BaseVerifier):
    """
    Validate result.structured against required_fields, a JSON Schema dict,
    or a Pydantic model path.

    At least one of schema or required_fields must be provided.
    """

    id: ClassVar[str] = "schema"
    description: ClassVar[str] = (
        "Validate structured output against required fields, JSON Schema, "
        "or a Pydantic model. Returns field-level error messages."
    )
    shareable: ClassVar[bool] = True  # stateless: schema is bound in __init__

    def __init__(
        self,
        schema: dict[str, Any] | str | None = None,
        required_fields: list[str] | None = None,
    ) -> None:
        """
        Args:
            schema: JSON Schema dict OR 'module.path:ClassName' Pydantic model path.
            required_fields: List of field names that must be present and non-null.

        At least one of schema or required_fields must be provided.
        """
        if schema is None and not required_fields:
            raise VeridianConfigError(
                "SchemaVerifier requires at least one of 'schema' or 'required_fields'. "
                "Provide a JSON Schema dict, a Pydantic model path, "
                "or a list of required field names."
            )
        # Fail closed: reject a malformed JSON Schema at construction time rather
        # than silently passing every payload at verify time.
        if isinstance(schema, dict):
            _validate_schema_or_raise(schema)
        self.schema = schema
        self.required_fields: list[str] = required_fields or []

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Validate result.structured against the configured schema."""
        data = result.structured
        errors: list[str] = []

        # 1. required_fields check (fast, no deps)
        for field in self.required_fields:
            if field not in data or data[field] is None:
                errors.append(f"required field '{field}' is missing or null")

        # 2. JSON Schema or Pydantic model validation
        if self.schema is not None:
            if isinstance(self.schema, dict):
                errors.extend(_check_json_schema(self.schema, data))
            elif isinstance(self.schema, str):
                errors.extend(_validate_pydantic(self.schema, data))

        if not errors:
            return VerificationResult(
                passed=True,
                evidence={"schema_checks": "all passed", "fields_checked": len(data)},
            )

        # Deduplicate and format errors
        unique_errors = list(dict.fromkeys(errors))
        field_errors = "; ".join(unique_errors[:3])  # at most 3 in error message
        error_msg = f"Schema validation failed. Missing/invalid: {field_errors}"[:300]

        return VerificationResult(
            passed=False,
            error=error_msg,
            evidence={"field_errors": unique_errors},
        )
