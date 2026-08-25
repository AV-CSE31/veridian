"""Immutable bindings captured for one logical verification decision."""

from __future__ import annotations

from dataclasses import dataclass

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    require_digest,
    require_exact_fields,
    require_string,
    require_string_tuple,
    sha256_digest,
)
from ._errors import AssuranceValidationError
from ._model import parse_utc_second

SNAPSHOT_SCHEMA_ID = "veridian.verification-snapshot.v1"


@dataclass(frozen=True)
class VerificationSnapshotV1:
    """Exact state, evidence, and verifier inputs evaluated by the kernel."""

    authorization_envelope_digest: str
    state_digest: str
    evidence_ref_digests: tuple[str, ...]
    verifier_manifest_digests: tuple[str, ...]
    captured_at: str

    def __post_init__(self) -> None:
        for field_name in ("authorization_envelope_digest", "state_digest"):
            object.__setattr__(
                self, field_name, require_digest(getattr(self, field_name), field_name)
            )
        for field_name in ("evidence_ref_digests", "verifier_manifest_digests"):
            values = require_string_tuple(getattr(self, field_name), field_name)
            object.__setattr__(
                self,
                field_name,
                tuple(require_digest(value, field_name) for value in values),
            )
        object.__setattr__(self, "captured_at", require_string(self.captured_at, "captured_at"))
        parse_utc_second(self.captured_at, "captured_at")
        if len(set(self.evidence_ref_digests)) != len(self.evidence_ref_digests):
            raise AssuranceValidationError("evidence_ref_digests must be unique")
        if len(set(self.verifier_manifest_digests)) != len(self.verifier_manifest_digests):
            raise AssuranceValidationError("verifier_manifest_digests must be unique")

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": SNAPSHOT_SCHEMA_ID,
                "authorization_envelope_digest": self.authorization_envelope_digest,
                "state_digest": self.state_digest,
                "evidence_ref_digests": self.evidence_ref_digests,
                "verifier_manifest_digests": self.verifier_manifest_digests,
                "captured_at": self.captured_at,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> VerificationSnapshotV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "authorization_envelope_digest",
                "state_digest",
                "evidence_ref_digests",
                "verifier_manifest_digests",
                "captured_at",
            }
        )
        require_exact_fields(payload, fields, "VerificationSnapshotV1")
        if payload["schema_id"] != SNAPSHOT_SCHEMA_ID:
            raise AssuranceValidationError("unsupported verification snapshot schema")
        return cls(
            authorization_envelope_digest=require_digest(
                payload["authorization_envelope_digest"], "authorization_envelope_digest"
            ),
            state_digest=require_digest(payload["state_digest"], "state_digest"),
            evidence_ref_digests=tuple(
                require_digest(value, "evidence_ref_digests")
                for value in require_string_tuple(
                    payload["evidence_ref_digests"], "evidence_ref_digests"
                )
            ),
            verifier_manifest_digests=tuple(
                require_digest(value, "verifier_manifest_digests")
                for value in require_string_tuple(
                    payload["verifier_manifest_digests"], "verifier_manifest_digests"
                )
            ),
            captured_at=require_string(payload["captured_at"], "captured_at"),
        )
