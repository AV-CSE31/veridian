"""Authoritative replay and independently governed history context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from veridian.core.exceptions import VeridianError

from ._attestation import (
    Signer,
    VerificationKeyProvider,
    _verify_envelope,
    sign_attestation,
)
from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    require_digest,
    require_exact_fields,
    require_string,
)
from ._errors import AssuranceValidationError
from ._model import AuthorizationEnvelope, parse_utc_second

WITNESS_SCHEMA_ID = "veridian.witness-statement.v1"
WITNESS_PAYLOAD_TYPE = "application/vnd.veridian.witness-statement.v1+json"


class NonceStatus(StrEnum):
    FRESH = "fresh"
    REDEEMED = "redeemed"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class ReplayStatus(StrEnum):
    NOT_CHECKED = "not-checked"
    FRESH = "fresh"
    WRONG_AUDIENCE = "wrong-audience"
    WRONG_PRINCIPAL = "wrong-principal"
    STATE_MISMATCH = "state-mismatch"
    NOT_YET_VALID = "not-yet-valid"
    EXPIRED = "expired"
    REDEEMED = "redeemed"
    REVOKED = "revoked"
    UNKNOWN_NONCE = "unknown-nonce"
    CONTEXT_ERROR = "context-error"


class HistoryStatus(StrEnum):
    UNANCHORED = "unanchored"
    ANCHORED = "anchored"
    WITNESSED = "witnessed"
    ANCHORED_AND_WITNESSED = "anchored-and-witnessed"
    MISMATCH = "mismatch"


class NonceRegistry(Protocol):
    """Authoritative, read-only replay-state seam."""

    def status(self, *, audience: str, nonce: str) -> NonceStatus: ...


@dataclass(frozen=True)
class ReplayContext:
    """Context required to turn offline integrity into replay authorization."""

    expected_audience: str
    expected_state_digest: str
    now: datetime
    nonce_registry: NonceRegistry
    expected_principal_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "expected_audience", require_string(self.expected_audience, "expected_audience")
        )
        object.__setattr__(
            self,
            "expected_state_digest",
            require_digest(self.expected_state_digest, "expected_state_digest"),
        )
        if self.expected_principal_id is not None:
            object.__setattr__(
                self,
                "expected_principal_id",
                require_string(self.expected_principal_id, "expected_principal_id"),
            )
        if not isinstance(self.now, datetime) or self.now.tzinfo is None:
            raise AssuranceValidationError("replay context now must be timezone-aware UTC")
        if self.now.utcoffset() != UTC.utcoffset(self.now):
            raise AssuranceValidationError("replay context now must use UTC")
        if not callable(getattr(self.nonce_registry, "status", None)):
            raise AssuranceValidationError(
                "nonce_registry must implement the authoritative status seam"
            )


def evaluate_replay(
    authorization: AuthorizationEnvelope, context: ReplayContext
) -> tuple[ReplayStatus, str | None]:
    if authorization.audience != context.expected_audience:
        return ReplayStatus.WRONG_AUDIENCE, "authorization audience does not match context"
    if (
        context.expected_principal_id is not None
        and authorization.principal_id != context.expected_principal_id
    ):
        return ReplayStatus.WRONG_PRINCIPAL, "authorization principal does not match context"
    if authorization.state_digest != context.expected_state_digest:
        return ReplayStatus.STATE_MISMATCH, "authorization state does not match context"
    if context.now < parse_utc_second(authorization.not_before, "not_before"):
        return ReplayStatus.NOT_YET_VALID, "authorization is not yet valid"
    if context.now >= parse_utc_second(authorization.expires_at, "expires_at"):
        return ReplayStatus.EXPIRED, "authorization has expired"
    try:
        nonce_status = context.nonce_registry.status(
            audience=authorization.audience, nonce=authorization.nonce
        )
    except VeridianError as exc:
        return ReplayStatus.CONTEXT_ERROR, str(exc)
    except Exception as exc:
        return ReplayStatus.CONTEXT_ERROR, f"nonce registry failed: {type(exc).__name__}"
    if nonce_status is NonceStatus.FRESH:
        return ReplayStatus.FRESH, None
    if nonce_status is NonceStatus.REDEEMED:
        return ReplayStatus.REDEEMED, "authorization nonce is already redeemed"
    if nonce_status is NonceStatus.REVOKED:
        return ReplayStatus.REVOKED, "authorization nonce is revoked"
    return ReplayStatus.UNKNOWN_NONCE, "authorization nonce state is unknown"


@dataclass(frozen=True)
class AnchorHead:
    """Independently retained monotonic head for one receipt stream."""

    stream_id: str
    sequence: int
    receipt_envelope_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", require_string(self.stream_id, "stream_id"))
        object.__setattr__(
            self,
            "receipt_envelope_digest",
            require_digest(self.receipt_envelope_digest, "receipt_envelope_digest"),
        )
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise AssuranceValidationError("anchor sequence must be a non-negative integer")


class AnchorStore(Protocol):
    """Independent read seam; implementations must not trust the proof's own store."""

    def trusted_head(self, stream_id: str) -> AnchorHead | None: ...


@dataclass(frozen=True)
class WitnessStatementV1:
    """A witness observation of one exact receipt-envelope head."""

    stream_id: str
    sequence: int
    receipt_envelope_digest: str
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", require_string(self.stream_id, "stream_id"))
        object.__setattr__(
            self,
            "receipt_envelope_digest",
            require_digest(self.receipt_envelope_digest, "receipt_envelope_digest"),
        )
        object.__setattr__(self, "observed_at", require_string(self.observed_at, "observed_at"))
        parse_utc_second(self.observed_at, "observed_at")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise AssuranceValidationError("witness sequence must be a non-negative integer")

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": WITNESS_SCHEMA_ID,
                "stream_id": self.stream_id,
                "sequence": self.sequence,
                "receipt_envelope_digest": self.receipt_envelope_digest,
                "observed_at": self.observed_at,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> WitnessStatementV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "stream_id",
                "sequence",
                "receipt_envelope_digest",
                "observed_at",
            }
        )
        require_exact_fields(payload, fields, "WitnessStatementV1")
        if payload["schema_id"] != WITNESS_SCHEMA_ID:
            raise AssuranceValidationError("unsupported witness statement schema")
        sequence = payload["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise AssuranceValidationError("witness sequence must be an integer")
        return cls(
            stream_id=require_string(payload["stream_id"], "stream_id"),
            sequence=sequence,
            receipt_envelope_digest=require_digest(
                payload["receipt_envelope_digest"], "receipt_envelope_digest"
            ),
            observed_at=require_string(payload["observed_at"], "observed_at"),
        )


def sign_witness(statement: WitnessStatementV1, signer: Signer | None) -> bytes:
    return sign_attestation(WITNESS_PAYLOAD_TYPE, statement.to_bytes(), signer)


@dataclass(frozen=True)
class AnchorContext:
    """Independent store and/or witness threshold used for history claims."""

    store: AnchorStore | None = None
    witness_keys: VerificationKeyProvider | None = None
    minimum_witnesses: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.minimum_witnesses, int)
            or isinstance(self.minimum_witnesses, bool)
            or self.minimum_witnesses < 0
        ):
            raise AssuranceValidationError("minimum_witnesses must be a non-negative integer")
        if self.minimum_witnesses and self.witness_keys is None:
            raise AssuranceValidationError("witness keys are required for a witness threshold")
        if self.store is None and self.minimum_witnesses == 0:
            raise AssuranceValidationError("anchor context must require a store or witnesses")
        if self.store is not None and not callable(getattr(self.store, "trusted_head", None)):
            raise AssuranceValidationError("anchor store must implement trusted_head")


@dataclass(frozen=True)
class ProofVerificationContext:
    """Optional external context strengthening a signed proof's guarantees."""

    replay: ReplayContext | None = None
    anchor: AnchorContext | None = None


def validate_witnesses(
    witness_envelopes: tuple[bytes, ...],
    *,
    expected: AnchorHead,
    context: AnchorContext,
) -> tuple[bool, set[str], str | None]:
    if context.minimum_witnesses == 0:
        return True, set(), None
    assert context.witness_keys is not None
    key_ids: set[str] = set()
    for envelope_bytes in witness_envelopes:
        try:
            envelope = _verify_envelope(envelope_bytes, context.witness_keys)
            if envelope.payload_type != WITNESS_PAYLOAD_TYPE:
                return False, key_ids, "witness has the wrong payload type"
            statement = WitnessStatementV1.from_bytes(envelope.payload)
        except VeridianError as exc:
            return False, key_ids, f"invalid witness: {exc}"
        if (
            statement.stream_id != expected.stream_id
            or statement.sequence != expected.sequence
            or statement.receipt_envelope_digest != expected.receipt_envelope_digest
        ):
            return False, key_ids, "witness does not bind the expected receipt head"
        key_ids.update(envelope.verified_key_ids)
    if len(key_ids) < context.minimum_witnesses:
        return False, key_ids, "witness threshold not met"
    return True, key_ids, None
