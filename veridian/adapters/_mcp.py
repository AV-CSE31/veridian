"""Adapter for MCP JSON-RPC ``tools/call`` requests."""

from __future__ import annotations

from collections.abc import Mapping

from ._base import (
    StrictActionAdapter,
    load_record,
    require_fields,
    require_literal,
    require_mapping,
)
from ._errors import AdapterValidationError
from ._model import ActionSpecV1, NormalizedActionV1


class MCPToolCallAdapter(StrictActionAdapter):
    """Normalize one MCP ``tools/call`` request without invoking a server.

    The negotiated MCP version is required explicitly because it does not
    reliably live in the JSON-RPC body. Extensible ``_meta`` stays transport
    provenance and cannot alter business semantics.
    """

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        protocol_version: str,
    ) -> None:
        super().__init__(
            specs,
            adapter_id="veridian.mcp.tools-call",
            adapter_version="1.0.0",
            protocol="mcp.json-rpc",
            protocol_version=protocol_version,
        )

    def normalize(self, message: object) -> NormalizedActionV1:
        record, raw_bytes = load_record(message)
        require_fields(
            record,
            required=frozenset({"jsonrpc", "id", "method", "params"}),
            name="MCP tools/call request",
        )
        require_literal(record["jsonrpc"], "2.0", "jsonrpc")
        require_literal(record["method"], "tools/call", "method")
        params = require_mapping(record["params"], "params")
        require_fields(
            params,
            required=frozenset({"name", "arguments"}),
            optional=frozenset({"_meta"}),
            name="MCP tools/call params",
        )
        if "_meta" in params:
            require_mapping(params["_meta"], "params._meta")
        arguments = require_mapping(params["arguments"], "params.arguments")
        return self._finish(
            external_name=params["name"],
            message_id=_json_rpc_message_id(record["id"]),
            arguments=arguments,
            raw_bytes=raw_bytes,
        )


def _json_rpc_message_id(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return f"jsonrpc-number:{value}"
    raise AdapterValidationError("JSON-RPC id must be a non-empty string or integer")
