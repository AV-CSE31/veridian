"""Adapter for LangGraph/LangChain-style tool-call records."""

from __future__ import annotations

from collections.abc import Mapping

from ._base import (
    StrictActionAdapter,
    load_record,
    require_fields,
    require_literal,
    require_mapping,
)
from ._model import ActionSpecV1, NormalizedActionV1


class LangGraphToolCallAdapter(StrictActionAdapter):
    """Normalize the portable ``{name, args, id, type}`` tool-call shape.

    Dicts and SDK-like attribute objects are accepted; no LangGraph or
    LangChain dependency is imported.
    """

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        protocol_version: str = "record-v1",
    ) -> None:
        super().__init__(
            specs,
            adapter_id="veridian.langgraph.tool-call",
            adapter_version="1.0.0",
            protocol="langgraph.tool-call",
            protocol_version=protocol_version,
        )

    def normalize(self, message: object) -> NormalizedActionV1:
        record, raw_bytes = load_record(message)
        require_fields(
            record,
            required=frozenset({"id", "name", "args", "type"}),
            name="LangGraph tool call",
        )
        require_literal(record["type"], "tool_call", "type")
        arguments = require_mapping(record["args"], "args")
        return self._finish(
            external_name=record["name"],
            message_id=record["id"],
            arguments=arguments,
            raw_bytes=raw_bytes,
        )
