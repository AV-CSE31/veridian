"""Opaque, privacy-aware evidence references."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    require_exact_fields,
    require_string,
    sha256_digest,
)
from ._errors import AssuranceValidationError
from ._model import parse_utc_second

EVIDENCE_SCHEMA_ID = "veridian.evidence-ref.v1"
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,}$")


class EvidenceTrust(StrEnum):
    """Declared trust relationship for an evidence producer."""

    UNVERIFIED = "unverified"
    ATTESTED = "attested"
    AUTHORITATIVE = "authoritative"


@dataclass(frozen=True)
class EvidenceRef:
    """A governed reference that never embeds evidence or a plaintext locator."""

    evidence_id: str
    schema_id: str
    media_type: str
    producer_id: str
    observed_at: str
    valid_until: str | None
    trust: EvidenceTrust
    tenant_id: str
    purpose: str
    access_class: str
    retention_class: str
    opaque_locator: str | None = None
    commitment_scheme: str | None = None
    commitment: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "schema_id",
            "media_type",
            "producer_id",
            "observed_at",
            "tenant_id",
            "purpose",
            "access_class",
            "retention_class",
        ):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        if not self.evidence_id.startswith("ev_") or len(self.evidence_id) < 19:
            raise AssuranceValidationError("evidence_id must be an opaque ev_ identifier")
        observed = parse_utc_second(self.observed_at, "observed_at")
        if self.valid_until is not None:
            valid_until = parse_utc_second(self.valid_until, "valid_until")
            if valid_until <= observed:
                raise AssuranceValidationError("valid_until must be later than observed_at")
        if not isinstance(self.trust, EvidenceTrust):
            raise AssuranceValidationError("trust must be an EvidenceTrust value")
        if self.opaque_locator is not None:
            locator = require_string(self.opaque_locator, "opaque_locator")
            token = locator.removeprefix("opaque:")
            if not locator.startswith("opaque:") or not _OPAQUE_TOKEN.fullmatch(token):
                raise AssuranceValidationError(
                    "opaque_locator must be an opaque: token, never a path or URL"
                )
        if (self.commitment_scheme is None) != (self.commitment is None):
            raise AssuranceValidationError(
                "commitment_scheme and commitment must be supplied together"
            )
        if self.commitment_scheme is not None:
            object.__setattr__(
                self,
                "commitment_scheme",
                require_string(self.commitment_scheme, "commitment_scheme"),
            )
            object.__setattr__(self, "commitment", require_string(self.commitment, "commitment"))

    @classmethod
    def new(
        cls,
        *,
        schema_id: str,
        media_type: str,
        producer_id: str,
        observed_at: str,
        valid_until: str | None,
        trust: EvidenceTrust,
        tenant_id: str,
        purpose: str,
        access_class: str,
        retention_class: str,
        opaque_locator: str | None = None,
        commitment_scheme: str | None = None,
        commitment: str | None = None,
    ) -> EvidenceRef:
        """Create a reference with a cryptographically random, correlation-resistant ID."""
        return cls(
            evidence_id=f"ev_{secrets.token_urlsafe(24)}",
            schema_id=schema_id,
            media_type=media_type,
            producer_id=producer_id,
            observed_at=observed_at,
            valid_until=valid_until,
            trust=trust,
            tenant_id=tenant_id,
            purpose=purpose,
            access_class=access_class,
            retention_class=retention_class,
            opaque_locator=opaque_locator,
            commitment_scheme=commitment_scheme,
            commitment=commitment,
        )

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": EVIDENCE_SCHEMA_ID,
                "evidence_id": self.evidence_id,
                "evidence_schema_id": self.schema_id,
                "media_type": self.media_type,
                "producer_id": self.producer_id,
                "observed_at": self.observed_at,
                "valid_until": self.valid_until,
                "trust": self.trust.value,
                "tenant_id": self.tenant_id,
                "purpose": self.purpose,
                "access_class": self.access_class,
                "retention_class": self.retention_class,
                "opaque_locator": self.opaque_locator,
                "commitment_scheme": self.commitment_scheme,
                "commitment": self.commitment,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> EvidenceRef:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "evidence_id",
                "evidence_schema_id",
                "media_type",
                "producer_id",
                "observed_at",
                "valid_until",
                "trust",
                "tenant_id",
                "purpose",
                "access_class",
                "retention_class",
                "opaque_locator",
                "commitment_scheme",
                "commitment",
            }
        )
        require_exact_fields(payload, fields, "EvidenceRef")
        if payload["schema_id"] != EVIDENCE_SCHEMA_ID:
            raise AssuranceValidationError("unsupported evidence reference schema")
        try:
            trust = EvidenceTrust(require_string(payload["trust"], "trust"))
        except ValueError as exc:
            raise AssuranceValidationError("unsupported evidence trust value") from exc
        optional_strings: dict[str, str | None] = {}
        for field_name in ("valid_until", "opaque_locator", "commitment_scheme", "commitment"):
            value = payload[field_name]
            if value is not None and not isinstance(value, str):
                raise AssuranceValidationError(f"{field_name} must be a string or null")
            optional_strings[field_name] = value
        return cls(
            evidence_id=require_string(payload["evidence_id"], "evidence_id"),
            schema_id=require_string(payload["evidence_schema_id"], "evidence_schema_id"),
            media_type=require_string(payload["media_type"], "media_type"),
            producer_id=require_string(payload["producer_id"], "producer_id"),
            observed_at=require_string(payload["observed_at"], "observed_at"),
            valid_until=optional_strings["valid_until"],
            trust=trust,
            tenant_id=require_string(payload["tenant_id"], "tenant_id"),
            purpose=require_string(payload["purpose"], "purpose"),
            access_class=require_string(payload["access_class"], "access_class"),
            retention_class=require_string(payload["retention_class"], "retention_class"),
            opaque_locator=optional_strings["opaque_locator"],
            commitment_scheme=optional_strings["commitment_scheme"],
            commitment=optional_strings["commitment"],
        )
