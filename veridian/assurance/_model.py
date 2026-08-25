"""Protocol-neutral semantic, authorization, and transport models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    freeze_mapping,
    require_digest,
    require_exact_fields,
    require_string,
    require_string_tuple,
    sha256_digest,
)
from ._errors import AssuranceValidationError

ACTION_SCHEMA_ID = "veridian.action-semantics.v1"
COMPLETION_SCHEMA_ID = "veridian.completion-semantics.v1"
AUTHORIZATION_SCHEMA_ID = "veridian.authorization-envelope.v1"
TRANSPORT_SCHEMA_ID = "veridian.transport-binding.v1"

_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def parse_utc_second(value: object, field_name: str) -> datetime:
    text = require_string(value, field_name)
    if not _RFC3339_UTC.fullmatch(text):
        raise AssuranceValidationError(f"{field_name} must be RFC 3339 UTC at second precision")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AssuranceValidationError(f"{field_name} is not a valid UTC timestamp") from exc


@dataclass(frozen=True)
class ActionSemanticsV1:
    """Protocol-neutral identity of a proposed business action."""

    action_type: str
    target: str
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_type", require_string(self.action_type, "action_type"))
        object.__setattr__(self, "target", require_string(self.target, "target"))
        object.__setattr__(self, "parameters", freeze_mapping(self.parameters, "parameters"))

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": ACTION_SCHEMA_ID,
                "action_type": self.action_type,
                "target": self.target,
                "parameters": self.parameters,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> ActionSemanticsV1:
        payload = decode_profile_v1(data)
        require_exact_fields(
            payload,
            frozenset({"schema_id", "action_type", "target", "parameters"}),
            "ActionSemanticsV1",
        )
        if payload["schema_id"] != ACTION_SCHEMA_ID:
            raise AssuranceValidationError("unsupported action semantics schema")
        parameters = payload["parameters"]
        if not isinstance(parameters, Mapping):
            raise AssuranceValidationError("parameters must be an object")
        return cls(
            require_string(payload["action_type"], "action_type"),
            require_string(payload["target"], "target"),
            cast(Mapping[str, object], parameters),
        )


@dataclass(frozen=True)
class CompletionSemanticsV1:
    """Protocol-neutral identity of a claimed business completion."""

    completion_type: str
    subject: str
    assertions: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "completion_type", require_string(self.completion_type, "completion_type")
        )
        object.__setattr__(self, "subject", require_string(self.subject, "subject"))
        object.__setattr__(self, "assertions", freeze_mapping(self.assertions, "assertions"))

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": COMPLETION_SCHEMA_ID,
                "completion_type": self.completion_type,
                "subject": self.subject,
                "assertions": self.assertions,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> CompletionSemanticsV1:
        payload = decode_profile_v1(data)
        require_exact_fields(
            payload,
            frozenset({"schema_id", "completion_type", "subject", "assertions"}),
            "CompletionSemanticsV1",
        )
        if payload["schema_id"] != COMPLETION_SCHEMA_ID:
            raise AssuranceValidationError("unsupported completion semantics schema")
        assertions = payload["assertions"]
        if not isinstance(assertions, Mapping):
            raise AssuranceValidationError("assertions must be an object")
        return cls(
            require_string(payload["completion_type"], "completion_type"),
            require_string(payload["subject"], "subject"),
            cast(Mapping[str, object], assertions),
        )


@dataclass(frozen=True)
class AuthorizationEnvelope:
    """Exact principal, delegation, purpose, state, and policy authorization."""

    semantic_kind: Literal["action", "completion"]
    semantic_digest: str
    principal_id: str
    delegation_chain: tuple[str, ...]
    audience: str
    purpose: str
    nonce: str
    not_before: str
    expires_at: str
    state_digest: str
    policy_digest: str

    def __post_init__(self) -> None:
        if self.semantic_kind not in ("action", "completion"):
            raise AssuranceValidationError("semantic_kind must be 'action' or 'completion'")
        for field_name in (
            "semantic_digest",
            "state_digest",
            "policy_digest",
        ):
            object.__setattr__(
                self, field_name, require_digest(getattr(self, field_name), field_name)
            )
        for field_name in ("principal_id", "audience", "purpose", "nonce"):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        if len(self.nonce) < 16:
            raise AssuranceValidationError("nonce must contain at least 16 characters")
        object.__setattr__(
            self,
            "delegation_chain",
            require_string_tuple(self.delegation_chain, "delegation_chain"),
        )
        start = parse_utc_second(self.not_before, "not_before")
        end = parse_utc_second(self.expires_at, "expires_at")
        if end <= start:
            raise AssuranceValidationError("expires_at must be later than not_before")

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": AUTHORIZATION_SCHEMA_ID,
                "semantic_kind": self.semantic_kind,
                "semantic_digest": self.semantic_digest,
                "principal_id": self.principal_id,
                "delegation_chain": self.delegation_chain,
                "audience": self.audience,
                "purpose": self.purpose,
                "nonce": self.nonce,
                "not_before": self.not_before,
                "expires_at": self.expires_at,
                "state_digest": self.state_digest,
                "policy_digest": self.policy_digest,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> AuthorizationEnvelope:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "semantic_kind",
                "semantic_digest",
                "principal_id",
                "delegation_chain",
                "audience",
                "purpose",
                "nonce",
                "not_before",
                "expires_at",
                "state_digest",
                "policy_digest",
            }
        )
        require_exact_fields(payload, fields, "AuthorizationEnvelope")
        if payload["schema_id"] != AUTHORIZATION_SCHEMA_ID:
            raise AssuranceValidationError("unsupported authorization envelope schema")
        kind = payload["semantic_kind"]
        if kind not in ("action", "completion"):
            raise AssuranceValidationError("invalid semantic_kind")
        return cls(
            semantic_kind=kind,
            semantic_digest=require_digest(payload["semantic_digest"], "semantic_digest"),
            principal_id=require_string(payload["principal_id"], "principal_id"),
            delegation_chain=require_string_tuple(payload["delegation_chain"], "delegation_chain"),
            audience=require_string(payload["audience"], "audience"),
            purpose=require_string(payload["purpose"], "purpose"),
            nonce=require_string(payload["nonce"], "nonce"),
            not_before=require_string(payload["not_before"], "not_before"),
            expires_at=require_string(payload["expires_at"], "expires_at"),
            state_digest=require_digest(payload["state_digest"], "state_digest"),
            policy_digest=require_digest(payload["policy_digest"], "policy_digest"),
        )


@dataclass(frozen=True)
class TransportBinding:
    """Transport provenance kept distinct from business semantics and authorization."""

    adapter_id: str
    adapter_version: str
    protocol: str
    protocol_version: str
    message_id: str
    raw_message_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "adapter_id",
            "adapter_version",
            "protocol",
            "protocol_version",
            "message_id",
        ):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "raw_message_digest",
            require_digest(self.raw_message_digest, "raw_message_digest"),
        )

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": TRANSPORT_SCHEMA_ID,
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "protocol": self.protocol,
                "protocol_version": self.protocol_version,
                "message_id": self.message_id,
                "raw_message_digest": self.raw_message_digest,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> TransportBinding:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "adapter_id",
                "adapter_version",
                "protocol",
                "protocol_version",
                "message_id",
                "raw_message_digest",
            }
        )
        require_exact_fields(payload, fields, "TransportBinding")
        if payload["schema_id"] != TRANSPORT_SCHEMA_ID:
            raise AssuranceValidationError("unsupported transport binding schema")
        return cls(
            adapter_id=require_string(payload["adapter_id"], "adapter_id"),
            adapter_version=require_string(payload["adapter_version"], "adapter_version"),
            protocol=require_string(payload["protocol"], "protocol"),
            protocol_version=require_string(payload["protocol_version"], "protocol_version"),
            message_id=require_string(payload["message_id"], "message_id"),
            raw_message_digest=require_digest(payload["raw_message_digest"], "raw_message_digest"),
        )
