"""Veridian Canonical JSON Profile v1.

The profile deliberately accepts less than general JSON: NFC strings, booleans,
null, arrays, objects with string keys, and integers in the interoperable
[-(2**53)+1, (2**53)-1] range. Floats, duplicate keys, non-NFC strings, and
non-string keys are rejected. Object keys are ordered by Unicode scalar value;
UTF-8 is used without a BOM and without insignificant whitespace.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from hashlib import sha256
from types import MappingProxyType
from typing import TypeAlias, cast

from ._errors import AssuranceValidationError

CanonicalScalar: TypeAlias = None | bool | int | str
CanonicalValue: TypeAlias = (
    CanonicalScalar | Mapping[str, "CanonicalValue"] | Sequence["CanonicalValue"]
)

_MAX_SAFE_INTEGER = (2**53) - 1


def _validated_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssuranceValidationError(f"{field_name} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise AssuranceValidationError(f"{field_name} must use NFC Unicode")
    return value


def _canonical_tree(value: object, path: str = "$") -> CanonicalValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise AssuranceValidationError(f"{path} integer is outside the profile range")
        return value
    if isinstance(value, float):
        raise AssuranceValidationError(f"{path} floats are not allowed by canonical profile v1")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise AssuranceValidationError(f"{path} must use NFC Unicode")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, CanonicalValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AssuranceValidationError(f"{path} object keys must be strings")
            if unicodedata.normalize("NFC", key) != key:
                raise AssuranceValidationError(f"{path} object keys must use NFC Unicode")
            normalized[key] = _canonical_tree(item, f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [_canonical_tree(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise AssuranceValidationError(f"{path} contains unsupported type {type(value).__name__}")


def encode_profile_v1(value: object) -> bytes:
    """Encode a value using Veridian Canonical JSON Profile v1."""
    tree = _canonical_tree(value)
    return json.dumps(
        tree,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_float(value: str) -> float:
    raise AssuranceValidationError(f"floating-point JSON number {value!r} is not allowed")


def _parse_int(value: str) -> int:
    parsed = int(value)
    if not -_MAX_SAFE_INTEGER <= parsed <= _MAX_SAFE_INTEGER:
        raise AssuranceValidationError("JSON integer is outside the profile range")
    return parsed


def _reject_constant(value: str) -> None:
    raise AssuranceValidationError(f"JSON constant {value!r} is not allowed")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssuranceValidationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def decode_profile_v1(data: bytes) -> Mapping[str, object]:
    """Strictly decode a canonical-profile object and reject alternate bytes."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssuranceValidationError("profile bytes must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise AssuranceValidationError(f"invalid canonical JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise AssuranceValidationError("profile root must be an object")
    tree = _canonical_tree(value)
    if encode_profile_v1(tree) != data:
        raise AssuranceValidationError("JSON bytes are not canonical profile v1")
    return cast(Mapping[str, object], tree)


def sha256_digest(data: bytes) -> str:
    """Return a domain-explicit SHA-256 digest string for exact bytes."""
    return f"sha256:{sha256(data).hexdigest()}"


def freeze_mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, CanonicalValue]:
    """Validate and recursively freeze a caller-provided canonical mapping."""
    tree = _canonical_tree(value, field_name)
    if not isinstance(tree, dict):  # defensive: Mapping always normalizes to dict
        raise AssuranceValidationError(f"{field_name} must be an object")
    return cast(Mapping[str, CanonicalValue], _freeze(tree))


def _freeze(value: CanonicalValue) -> CanonicalValue:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def require_exact_fields(
    payload: Mapping[str, object], required: frozenset[str], object_name: str
) -> None:
    missing = sorted(required - payload.keys())
    extra = sorted(payload.keys() - required)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"unknown={extra}")
        raise AssuranceValidationError(f"invalid {object_name} fields: {', '.join(details)}")


def require_digest(value: object, field_name: str) -> str:
    digest = _validated_string(value, field_name)
    if len(digest) != 71 or not digest.startswith("sha256:"):
        raise AssuranceValidationError(f"{field_name} must be a sha256:<64 lowercase hex> digest")
    suffix = digest.removeprefix("sha256:")
    if any(character not in "0123456789abcdef" for character in suffix):
        raise AssuranceValidationError(f"{field_name} must be a sha256:<64 lowercase hex> digest")
    return digest


def require_string(value: object, field_name: str) -> str:
    return _validated_string(value, field_name)


def require_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise AssuranceValidationError(f"{field_name} must be an array of strings")
    return tuple(_validated_string(item, field_name) for item in value)
