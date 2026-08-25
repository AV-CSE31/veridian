"""Shared strict decoding for dependency-free adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import cast

from veridian.assurance import (
    ActionSemanticsV1,
    AssuranceError,
    TransportBinding,
    decode_profile_v1,
    encode_profile_v1,
    sha256_digest,
)

from ._errors import AdapterValidationError, UnknownActionError
from ._model import ActionSpecV1, NormalizedActionV1, _require_profile_name


class StrictActionAdapter:
    """Implementation shared by adapters with exact, versioned wire profiles."""

    adapter_id: str
    adapter_version: str
    protocol: str
    protocol_version: str

    def __init__(
        self,
        specs: Mapping[str, ActionSpecV1],
        *,
        adapter_id: str,
        adapter_version: str,
        protocol: str,
        protocol_version: str,
    ) -> None:
        if not isinstance(specs, Mapping) or not specs:
            raise AdapterValidationError("specs must contain at least one registered action")
        checked: dict[str, ActionSpecV1] = {}
        for external_name, spec in specs.items():
            name = _require_profile_name(external_name, "external action name")
            if not isinstance(spec, ActionSpecV1):
                raise AdapterValidationError("every action spec must be ActionSpecV1")
            checked[name] = spec
        self._specs = MappingProxyType(checked)
        self.adapter_id = _require_profile_name(adapter_id, "adapter_id")
        self.adapter_version = _require_profile_name(adapter_version, "adapter_version")
        self.protocol = _require_profile_name(protocol, "protocol")
        self.protocol_version = _require_profile_name(protocol_version, "protocol_version")

    def _finish(
        self,
        *,
        external_name: object,
        message_id: object,
        arguments: object,
        raw_bytes: bytes,
    ) -> NormalizedActionV1:
        name = _require_profile_name(external_name, "action name")
        identifier = _require_profile_name(message_id, "message_id")
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownActionError(f"action {name!r} is not registered")
        if not isinstance(arguments, Mapping):
            raise AdapterValidationError("action arguments must be an object")
        target = arguments.get(spec.target_parameter)
        target_text = _require_profile_name(target, spec.target_parameter)
        try:
            semantics = ActionSemanticsV1(spec.action_type, target_text, arguments)
            transport = TransportBinding(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                protocol=self.protocol,
                protocol_version=self.protocol_version,
                message_id=identifier,
                raw_message_digest=sha256_digest(raw_bytes),
            )
        except AssuranceError as exc:
            raise AdapterValidationError(str(exc)) from exc
        return NormalizedActionV1(semantics=semantics, transport=transport)


def load_record(message: object) -> tuple[Mapping[str, object], bytes]:
    """Return an exact profile object and the bytes bound into transport provenance."""
    try:
        if isinstance(message, bytes):
            return decode_profile_v1(message), message
        plain = _plain_value(message, "message")
        if not isinstance(plain, Mapping):
            raise AdapterValidationError("message must be an object or canonical JSON bytes")
        raw_bytes = encode_profile_v1(plain)
        return cast(Mapping[str, object], plain), raw_bytes
    except AdapterValidationError:
        raise
    except AssuranceError as exc:
        raise AdapterValidationError(str(exc)) from exc


def decode_arguments(value: object) -> Mapping[str, object]:
    """Decode an exact canonical-profile argument object."""
    if not isinstance(value, str) or not value:
        raise AdapterValidationError("arguments must be a non-empty canonical JSON object string")
    try:
        return decode_profile_v1(value.encode("utf-8"))
    except (UnicodeEncodeError, AssuranceError) as exc:
        raise AdapterValidationError("arguments must use canonical JSON profile v1") from exc


def require_fields(
    record: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    name: str,
) -> None:
    missing = sorted(required - record.keys())
    unknown = sorted(record.keys() - required - optional)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise AdapterValidationError(f"invalid {name} fields: {', '.join(details)}")


def require_literal(value: object, expected: str, field_name: str) -> None:
    if value != expected:
        raise AdapterValidationError(f"{field_name} must be {expected!r}")


def require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AdapterValidationError(f"{field_name} must be an object")
    return cast(Mapping[str, object], value)


def _plain_value(value: object, path: str) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {key: _plain_value(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [_plain_value(item, f"{path}[]") for item in value]
    try:
        attributes = vars(value)
    except TypeError as exc:
        raise AdapterValidationError(
            f"{path} contains unsupported object type {type(value).__name__}"
        ) from exc
    public = {key: item for key, item in attributes.items() if not key.startswith("_")}
    if len(public) != len(attributes):
        raise AdapterValidationError(f"{path} contains private or implementation fields")
    return {key: _plain_value(item, f"{path}.{key}") for key, item in public.items()}
