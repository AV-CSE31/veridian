"""
veridian.verify.builtin.schema
──────────────────────────────
SchemaVerifier — validate result.structured against a JSON Schema dict
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

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


def _check_json_schema(schema: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """
    Minimal JSON Schema validation without external dependencies.
    Handles: required, properties.type, properties.enum.
    Returns list of error strings.
    """
    errors: list[str] = []

    required: list[str] = schema.get("required", [])
    for field in required:
        if field not in data or data[field] is None:
            errors.append(f"required field '{field}' is missing or null")

    properties: dict[str, Any] = schema.get("properties", {})
    for field, constraints in properties.items():
        if field not in data:
            continue
        value = data[field]

        # type check
        expected_type: str | None = constraints.get("type")
        if expected_type:
            type_map: dict[str, type | tuple[type, ...]] = {
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
                "array": list,
                "object": dict,
                "null": type(None),
            }
            py_type = type_map.get(expected_type)
            if py_type and not isinstance(value, py_type):
                errors.append(
                    f"field '{field}' must be type '{expected_type}', got '{type(value).__name__}'"
                )

        # enum check
        enum_vals: list[Any] | None = constraints.get("enum")
        if enum_vals is not None and value not in enum_vals:
            errors.append(f"field '{field}' must be one of {enum_vals!r}, got '{value}'")

        # minLength / maxLength for strings
        if isinstance(value, str):
            min_len: int | None = constraints.get("minLength")
            max_len: int | None = constraints.get("maxLength")
            if min_len is not None and len(value) < min_len:
                errors.append(f"field '{field}' is too short (min {min_len} chars)")
            if max_len is not None and len(value) > max_len:
                errors.append(f"field '{field}' is too long (max {max_len} chars)")

        # minimum / maximum for numbers
        if isinstance(value, (int, float)):
            minimum: float | None = constraints.get("minimum")
            maximum: float | None = constraints.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(f"field '{field}' value {value} is below minimum {minimum}")
            if maximum is not None and value > maximum:
                errors.append(f"field '{field}' value {value} exceeds maximum {maximum}")

    return errors


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
