"""Adapter for OpenAI Responses API function-call output items."""

from __future__ import annotations

from collections.abc import Mapping

from ._base import (
    StrictActionAdapter,
    decode_arguments,
    load_record,
    require_fields,
    require_literal,
    require_mapping,
)
from ._errors import AdapterValidationError
from ._model import ActionSpecV1, NormalizedActionV1, _require_profile_name


class OpenAIResponsesAdapter(StrictActionAdapter):
    """Normalize one completed Responses function-call item without executing it.

    ``arguments`` must be a canonical-profile JSON object string. Known
    Responses provenance fields are retained in the raw-message digest.
    """

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        protocol_version: str = "v1",
    ) -> None:
        super().__init__(
            specs,
            adapter_id="veridian.openai.responses",
            adapter_version="1.0.0",
            protocol="openai.responses",
            protocol_version=protocol_version,
        )

    def normalize(self, message: object) -> NormalizedActionV1:
        record, raw_bytes = load_record(message)
        require_fields(
            record,
            required=frozenset({"type", "call_id", "name", "arguments"}),
            optional=frozenset({"id", "caller", "namespace", "status"}),
            name="OpenAI function call",
        )
        require_literal(record["type"], "function_call", "type")
        if record.get("id") is not None:
            _require_profile_name(record["id"], "id")
        if record.get("status") is not None:
            require_literal(record["status"], "completed", "status")
        if record.get("caller") is not None:
            self._validate_caller(record["caller"])
        external_name = _require_profile_name(record["name"], "name")
        if record.get("namespace") is not None:
            namespace = _require_profile_name(record["namespace"], "namespace")
            external_name = f"{namespace}.{external_name}"
        arguments = decode_arguments(record["arguments"])
        return self._finish(
            external_name=external_name,
            message_id=record["call_id"],
            arguments=arguments,
            raw_bytes=raw_bytes,
        )

    @staticmethod
    def _validate_caller(value: object) -> None:
        caller = require_mapping(value, "caller")
        caller_type = caller.get("type")
        if caller_type == "direct":
            require_fields(
                caller,
                required=frozenset({"type"}),
                name="OpenAI direct caller",
            )
            return
        if caller_type == "program":
            require_fields(
                caller,
                required=frozenset({"type", "caller_id"}),
                name="OpenAI program caller",
            )
            _require_profile_name(caller["caller_id"], "caller_id")
            return
        raise AdapterValidationError("caller.type must be 'direct' or 'program'")
