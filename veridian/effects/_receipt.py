"""Protocol-neutral postcondition statements for trusted effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from veridian.assurance import decode_profile_v1, encode_profile_v1, sha256_digest
from veridian.assurance._canonical import require_digest, require_exact_fields, require_string
from veridian.assurance._model import parse_utc_second

from ._errors import EffectValidationError

EFFECT_RECEIPT_SCHEMA_ID = "veridian.effect-receipt.v1"
EFFECT_RECEIPT_PAYLOAD_TYPE = "application/vnd.veridian.effect-receipt.v1+json"


class EffectReceiptType(StrEnum):
    """Observed effect milestone asserted by a trusted producer."""

    REDEEMED = "redeemed"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    COMMITTED = "committed"
    FAILED = "failed"
    COMPENSATED = "compensated"


def _string(value: object, field_name: str) -> str:
    try:
        return require_string(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


def _digest(value: object, field_name: str) -> str:
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise EffectValidationError(str(exc)) from exc


def _optional_digest(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _digest(value, field_name)


@dataclass(frozen=True)
class EffectReceiptV1:
    """Exact, privacy-preserving assertion about an observed side effect."""

    receipt_id: str
    receipt_type: EffectReceiptType
    effect_id: str
    semantic_digest: str
    authorization_envelope_digest: str
    permit_digest: str
    outbox_id: str
    producer_id: str
    observed_at: str
    external_reference_digest: str
    result_digest: str
    previous_receipt_digest: str | None

    def __post_init__(self) -> None:
        for field_name in ("receipt_id", "effect_id", "outbox_id", "producer_id"):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        if not isinstance(self.receipt_type, EffectReceiptType):
            try:
                object.__setattr__(
                    self,
                    "receipt_type",
                    EffectReceiptType(_string(self.receipt_type, "receipt_type")),
                )
            except ValueError as exc:
                raise EffectValidationError("unsupported effect receipt type") from exc
        for field_name in (
            "semantic_digest",
            "authorization_envelope_digest",
            "permit_digest",
            "external_reference_digest",
            "result_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "previous_receipt_digest",
            _optional_digest(self.previous_receipt_digest, "previous_receipt_digest"),
        )
        try:
            parse_utc_second(self.observed_at, "observed_at")
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": EFFECT_RECEIPT_SCHEMA_ID,
                "receipt_id": self.receipt_id,
                "receipt_type": self.receipt_type.value,
                "effect_id": self.effect_id,
                "semantic_digest": self.semantic_digest,
                "authorization_envelope_digest": self.authorization_envelope_digest,
                "permit_digest": self.permit_digest,
                "outbox_id": self.outbox_id,
                "producer_id": self.producer_id,
                "observed_at": self.observed_at,
                "external_reference_digest": self.external_reference_digest,
                "result_digest": self.result_digest,
                "previous_receipt_digest": self.previous_receipt_digest,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> EffectReceiptV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "receipt_id",
                "receipt_type",
                "effect_id",
                "semantic_digest",
                "authorization_envelope_digest",
                "permit_digest",
                "outbox_id",
                "producer_id",
                "observed_at",
                "external_reference_digest",
                "result_digest",
                "previous_receipt_digest",
            }
        )
        try:
            require_exact_fields(payload, fields, "EffectReceiptV1")
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        if payload["schema_id"] != EFFECT_RECEIPT_SCHEMA_ID:
            raise EffectValidationError("unsupported effect receipt schema")
        try:
            receipt_type = EffectReceiptType(_string(payload["receipt_type"], "receipt_type"))
        except ValueError as exc:
            raise EffectValidationError("unsupported effect receipt type") from exc
        return cls(
            receipt_id=_string(payload["receipt_id"], "receipt_id"),
            receipt_type=receipt_type,
            effect_id=_string(payload["effect_id"], "effect_id"),
            semantic_digest=_digest(payload["semantic_digest"], "semantic_digest"),
            authorization_envelope_digest=_digest(
                payload["authorization_envelope_digest"],
                "authorization_envelope_digest",
            ),
            permit_digest=_digest(payload["permit_digest"], "permit_digest"),
            outbox_id=_string(payload["outbox_id"], "outbox_id"),
            producer_id=_string(payload["producer_id"], "producer_id"),
            observed_at=_string(payload["observed_at"], "observed_at"),
            external_reference_digest=_digest(
                payload["external_reference_digest"],
                "external_reference_digest",
            ),
            result_digest=_digest(payload["result_digest"], "result_digest"),
            previous_receipt_digest=_optional_digest(
                payload["previous_receipt_digest"], "previous_receipt_digest"
            ),
        )
