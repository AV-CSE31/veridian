"""Adapter for Chit's minimal generic callable envelope."""

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


class GenericActionAdapter(StrictActionAdapter):
    """Normalize a dependency-free action envelope without invoking a callable."""

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        protocol_version: str = "1",
    ) -> None:
        super().__init__(
            specs,
            adapter_id="chit.generic-action",
            adapter_version="1.0.0",
            protocol="chit.generic-action",
            protocol_version=protocol_version,
        )

    def normalize(self, message: object) -> NormalizedActionV1:
        record, raw_bytes = load_record(message)
        require_fields(
            record,
            required=frozenset({"schema_id", "message_id", "action", "arguments"}),
            name="generic action envelope",
        )
        require_literal(record["schema_id"], "chit.generic-action.v1", "schema_id")
        arguments = require_mapping(record["arguments"], "arguments")
        return self._finish(
            external_name=record["action"],
            message_id=record["message_id"],
            arguments=arguments,
            raw_bytes=raw_bytes,
        )
