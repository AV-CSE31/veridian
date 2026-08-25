"""Versioned profile for Pydantic AI deferred tool requests.

Pydantic AI exposes deferred requests as two lists, ``approvals`` and ``calls``.
Each entry is a ``ToolCallPart`` with ``tool_name``, validated ``args``, and a
unique ``tool_call_id``.  This adapter deliberately accepts one entry at a time
through Veridian's stable profile instead of depending on Pydantic AI's Python
serialization details::

    {
      "schema_id": "veridian.pydantic-ai.deferred-tool.v1",
      "request_kind": "approval" | "external",
      "tool_call": {
        "tool_name": "...",
        "args": {...},
        "tool_call_id": "..."
      }
    }

The caller selects ``request_kind`` based on whether the part came from
``DeferredToolRequests.approvals`` or ``DeferredToolRequests.calls``.  The
adapter validates and normalizes only; it never approves or executes a tool.
"""

from __future__ import annotations

from collections.abc import Mapping

from ._base import StrictActionAdapter, load_record, require_fields, require_mapping
from ._errors import AdapterValidationError
from ._model import ActionSpecV1, NormalizedActionV1

PYDANTIC_AI_DEFERRED_TOOL_PROFILE_V1 = "veridian.pydantic-ai.deferred-tool.v1"


class PydanticAIDeferredToolAdapter(StrictActionAdapter):
    """Normalize one approval/external ``ToolCallPart`` through a stable profile."""

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        protocol_version: str = "v1",
    ) -> None:
        super().__init__(
            specs,
            adapter_id="veridian.pydantic-ai.deferred-tool",
            adapter_version="1.0.0",
            protocol="pydantic-ai.deferred-tool",
            protocol_version=protocol_version,
        )

    def normalize(self, message: object) -> NormalizedActionV1:
        record, raw_bytes = load_record(message)
        require_fields(
            record,
            required=frozenset({"schema_id", "request_kind", "tool_call"}),
            name="Pydantic AI deferred-tool profile",
        )
        if record["schema_id"] != PYDANTIC_AI_DEFERRED_TOOL_PROFILE_V1:
            raise AdapterValidationError(
                f"schema_id must be {PYDANTIC_AI_DEFERRED_TOOL_PROFILE_V1!r}"
            )
        kind = record["request_kind"]
        if kind not in ("approval", "external"):
            raise AdapterValidationError("request_kind must be 'approval' or 'external'")
        tool_call = require_mapping(record["tool_call"], "tool_call")
        require_fields(
            tool_call,
            required=frozenset({"tool_name", "args", "tool_call_id"}),
            name="Pydantic AI ToolCallPart profile",
        )
        arguments = require_mapping(tool_call["args"], "tool_call.args")
        return self._finish(
            external_name=tool_call["tool_name"],
            message_id=tool_call["tool_call_id"],
            arguments=arguments,
            raw_bytes=raw_bytes,
        )
