"""Immutable effect events and their deterministic state reduction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from veridian.assurance import decode_profile_v1, encode_profile_v1, sha256_digest
from veridian.assurance._canonical import (
    require_digest,
    require_exact_fields,
    require_string,
)
from veridian.assurance._model import parse_utc_second

from ._errors import EffectValidationError

EFFECT_EVENT_SCHEMA_ID = "veridian.effect-event.v1"


class EffectEventType(StrEnum):
    """Auditable milestones at the trusted side-effect boundary."""

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    PERMIT_ISSUED = "permit_issued"
    PERMIT_REDEEMED = "permit_redeemed"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    COMMITTED = "committed"
    FAILED = "failed"
    COMPENSATED = "compensated"
    EXPIRED = "expired"


class EffectStatus(StrEnum):
    """State obtained by reducing a valid effect-event trajectory."""

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    PERMIT_ISSUED = "permit_issued"
    PERMIT_REDEEMED = "permit_redeemed"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    COMMITTED = "committed"
    FAILED = "failed"
    COMPENSATED = "compensated"
    EXPIRED = "expired"


_PERMIT_EVENTS = frozenset(
    {
        EffectEventType.PERMIT_ISSUED,
        EffectEventType.PERMIT_REDEEMED,
        EffectEventType.DISPATCHED,
        EffectEventType.ACKNOWLEDGED,
        EffectEventType.COMMITTED,
        EffectEventType.COMPENSATED,
    }
)
_RECEIPT_REQUIRED = frozenset(
    {
        EffectEventType.ACKNOWLEDGED,
        EffectEventType.COMMITTED,
        EffectEventType.COMPENSATED,
    }
)


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        return require_string(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


def _optional_digest(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


def _required_digest(value: object, field_name: str) -> str:
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


def _required_string(value: object, field_name: str) -> str:
    try:
        return require_string(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


@dataclass(frozen=True)
class EffectEventV1:
    """One canonical, immutable observation in an effect trajectory."""

    event_id: str
    effect_id: str
    sequence: int
    event_type: EffectEventType
    occurred_at: str
    actor_id: str
    semantic_digest: str
    authorization_envelope_digest: str | None
    permit_id: str | None
    receipt_digest: str | None
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_string(self.event_id, "event_id"))
        object.__setattr__(self, "effect_id", _required_string(self.effect_id, "effect_id"))
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise EffectValidationError("sequence must be a non-negative integer")
        if not isinstance(self.event_type, EffectEventType):
            try:
                object.__setattr__(self, "event_type", EffectEventType(self.event_type))
            except (TypeError, ValueError) as exc:
                raise EffectValidationError("event_type is not supported") from exc
        object.__setattr__(self, "actor_id", _required_string(self.actor_id, "actor_id"))
        object.__setattr__(
            self, "semantic_digest", _required_digest(self.semantic_digest, "semantic_digest")
        )
        try:
            parse_utc_second(self.occurred_at, "occurred_at")
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        object.__setattr__(
            self,
            "authorization_envelope_digest",
            _optional_digest(
                self.authorization_envelope_digest,
                "authorization_envelope_digest",
            ),
        )
        object.__setattr__(self, "permit_id", _optional_string(self.permit_id, "permit_id"))
        object.__setattr__(
            self,
            "receipt_digest",
            _optional_digest(self.receipt_digest, "receipt_digest"),
        )
        if self.event_type is EffectEventType.PROPOSED:
            if self.authorization_envelope_digest is not None:
                raise EffectValidationError("PROPOSED must not claim an authorization envelope")
        elif (
            self.event_type not in {EffectEventType.FAILED, EffectEventType.EXPIRED}
            and self.authorization_envelope_digest is None
        ):
            raise EffectValidationError(f"{self.event_type.name} requires authorization envelope")
        if self.event_type in _PERMIT_EVENTS and self.permit_id is None:
            raise EffectValidationError(f"{self.event_type.name} requires permit_id")
        if self.event_type in _RECEIPT_REQUIRED and self.receipt_digest is None:
            raise EffectValidationError(f"{self.event_type.name} requires receipt_digest")
        if not isinstance(self.details, Mapping):
            raise EffectValidationError("details must be an object")
        try:
            normalized = decode_profile_v1(encode_profile_v1(dict(self.details)))
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        object.__setattr__(self, "details", normalized)

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": EFFECT_EVENT_SCHEMA_ID,
                "event_id": self.event_id,
                "effect_id": self.effect_id,
                "sequence": self.sequence,
                "event_type": self.event_type.value,
                "occurred_at": self.occurred_at,
                "actor_id": self.actor_id,
                "semantic_digest": self.semantic_digest,
                "authorization_envelope_digest": self.authorization_envelope_digest,
                "permit_id": self.permit_id,
                "receipt_digest": self.receipt_digest,
                "details": self.details,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> EffectEventV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "event_id",
                "effect_id",
                "sequence",
                "event_type",
                "occurred_at",
                "actor_id",
                "semantic_digest",
                "authorization_envelope_digest",
                "permit_id",
                "receipt_digest",
                "details",
            }
        )
        try:
            require_exact_fields(payload, fields, "EffectEventV1")
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        if payload["schema_id"] != EFFECT_EVENT_SCHEMA_ID:
            raise EffectValidationError("unsupported effect event schema")
        details = payload["details"]
        if not isinstance(details, Mapping):
            raise EffectValidationError("details must be an object")
        sequence = payload["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise EffectValidationError("sequence must be an integer")
        try:
            event_type = EffectEventType(_required_string(payload["event_type"], "event_type"))
        except (TypeError, ValueError) as exc:
            raise EffectValidationError("event_type is not supported") from exc
        return cls(
            event_id=_required_string(payload["event_id"], "event_id"),
            effect_id=_required_string(payload["effect_id"], "effect_id"),
            sequence=sequence,
            event_type=event_type,
            occurred_at=_required_string(payload["occurred_at"], "occurred_at"),
            actor_id=_required_string(payload["actor_id"], "actor_id"),
            semantic_digest=_required_digest(payload["semantic_digest"], "semantic_digest"),
            authorization_envelope_digest=_optional_digest(
                payload["authorization_envelope_digest"],
                "authorization_envelope_digest",
            ),
            permit_id=_optional_string(payload["permit_id"], "permit_id"),
            receipt_digest=_optional_digest(payload["receipt_digest"], "receipt_digest"),
            details=cast(Mapping[str, object], details),
        )


_ALLOWED_TRANSITIONS: Mapping[EffectEventType, frozenset[EffectEventType]] = {
    EffectEventType.PROPOSED: frozenset(
        {EffectEventType.AUTHORIZED, EffectEventType.FAILED, EffectEventType.EXPIRED}
    ),
    EffectEventType.AUTHORIZED: frozenset(
        {EffectEventType.PERMIT_ISSUED, EffectEventType.FAILED, EffectEventType.EXPIRED}
    ),
    EffectEventType.PERMIT_ISSUED: frozenset(
        {EffectEventType.PERMIT_REDEEMED, EffectEventType.EXPIRED}
    ),
    EffectEventType.PERMIT_REDEEMED: frozenset(
        {EffectEventType.DISPATCHED, EffectEventType.FAILED}
    ),
    EffectEventType.DISPATCHED: frozenset(
        {
            EffectEventType.ACKNOWLEDGED,
            EffectEventType.COMMITTED,
            EffectEventType.FAILED,
            EffectEventType.COMPENSATED,
        }
    ),
    EffectEventType.ACKNOWLEDGED: frozenset(
        {
            EffectEventType.COMMITTED,
            EffectEventType.FAILED,
            EffectEventType.COMPENSATED,
        }
    ),
    EffectEventType.FAILED: frozenset(),
    EffectEventType.COMMITTED: frozenset(),
    EffectEventType.COMPENSATED: frozenset(),
    EffectEventType.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class EffectState:
    """Materialized state of one valid, idempotently reduced trajectory."""

    effect_id: str
    semantic_digest: str
    authorization_envelope_digest: str | None
    permit_id: str | None
    status: EffectStatus
    event_count: int
    last_sequence: int
    head_digest: str

    @property
    def terminal(self) -> bool:
        return self.status in {
            EffectStatus.COMMITTED,
            EffectStatus.FAILED,
            EffectStatus.COMPENSATED,
            EffectStatus.EXPIRED,
        }


def reduce_effects(events: Iterable[EffectEventV1]) -> EffectState:
    """Reduce one ordered trajectory; exact retries are idempotent."""

    unique: list[EffectEventV1] = []
    seen: dict[str, str] = {}
    for event in events:
        if not isinstance(event, EffectEventV1):
            raise EffectValidationError("trajectory contains a non-EffectEventV1 value")
        previous_digest = seen.get(event.event_id)
        if previous_digest is not None:
            if previous_digest != event.digest:
                raise EffectValidationError(f"conflicting event_id: {event.event_id}")
            continue
        seen[event.event_id] = event.digest
        unique.append(event)

    if not unique:
        raise EffectValidationError("trajectory must contain at least one event")
    first = unique[0]
    if first.sequence != 0 or first.event_type is not EffectEventType.PROPOSED:
        raise EffectValidationError("trajectory sequence 0 must be PROPOSED")

    effect_id = first.effect_id
    semantic_digest = first.semantic_digest
    authorization_digest: str | None = None
    permit_id: str | None = None
    previous = first
    head_digest = sha256_digest(first.digest.encode("ascii"))

    for expected_sequence, event in enumerate(unique):
        if event.sequence != expected_sequence:
            raise EffectValidationError(
                f"trajectory sequence must be contiguous; expected {expected_sequence}"
            )
        if event.effect_id != effect_id:
            raise EffectValidationError("trajectory contains more than one effect_id")
        if event.semantic_digest != semantic_digest:
            raise EffectValidationError("semantic_digest changed within trajectory")
        if expected_sequence > 0:
            allowed = _ALLOWED_TRANSITIONS[previous.event_type]
            if event.event_type not in allowed:
                expected = ", ".join(item.name for item in sorted(allowed, key=lambda x: x.value))
                raise EffectValidationError(
                    f"invalid transition after {previous.event_type.name}; expected {expected or 'terminal'}"
                )
            if authorization_digest is None:
                authorization_digest = event.authorization_envelope_digest
            elif event.authorization_envelope_digest != authorization_digest:
                raise EffectValidationError("authorization envelope changed within trajectory")
            if event.permit_id is not None:
                if permit_id is None:
                    permit_id = event.permit_id
                elif event.permit_id != permit_id:
                    raise EffectValidationError("permit_id changed within trajectory")
            elif permit_id is not None:
                raise EffectValidationError("permit_id disappeared within trajectory")
            head_digest = sha256_digest(f"{head_digest}\n{event.digest}".encode("ascii"))
            previous = event

    return EffectState(
        effect_id=effect_id,
        semantic_digest=semantic_digest,
        authorization_envelope_digest=authorization_digest,
        permit_id=permit_id,
        status=EffectStatus(previous.event_type.value),
        event_count=len(unique),
        last_sequence=previous.sequence,
        head_digest=head_digest,
    )
