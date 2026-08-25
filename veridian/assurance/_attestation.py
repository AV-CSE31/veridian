"""Exact-byte Ed25519/DSSE-style receipt attestation."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    require_digest,
    require_exact_fields,
    require_string,
)
from ._errors import (
    AssuranceDependencyError,
    AssuranceValidationError,
    AssuranceVerificationError,
)
from ._model import parse_utc_second

RECEIPT_SCHEMA_ID = "veridian.receipt-statement.v1"
RECEIPT_PAYLOAD_TYPE = "application/vnd.veridian.receipt-statement.v1+json"
DSSE_ENVELOPE_SCHEMA_ID = "veridian.dsse-envelope.v1"


class Signer(Protocol):
    """Explicit signing seam for local, KMS, HSM, or transparency adapters."""

    @property
    def key_id(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


class VerificationKeyProvider(Protocol):
    """Explicit trust-root lookup; missing keys never fall back."""

    def public_key(self, key_id: str, algorithm: str) -> bytes | None: ...


class _PrivateKey(Protocol):
    def sign(self, data: bytes) -> bytes: ...

    def public_key(self) -> object: ...


@dataclass(frozen=True)
class Ed25519Signer:
    """Local production-capable Ed25519 reference signer.

    The cryptography backend is loaded only when this adapter is selected.
    Production deployments may instead provide a KMS/HSM ``Signer``.
    """

    _key_id: str
    _private_key: _PrivateKey

    def __post_init__(self) -> None:
        require_string(self._key_id, "key_id")

    @classmethod
    def generate(cls, key_id: str) -> Ed25519Signer:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as exc:  # pragma: no cover - exercised in dependency-minimal wheels
            raise AssuranceDependencyError(
                "Ed25519Signer requires the 'cryptography' package"
            ) from exc
        return cls(key_id, cast(_PrivateKey, Ed25519PrivateKey.generate()))

    @classmethod
    def from_private_bytes(cls, key_id: str, private_key: bytes) -> Ed25519Signer:
        if not isinstance(private_key, bytes) or len(private_key) != 32:
            raise AssuranceValidationError("Ed25519 private key seed must be exactly 32 bytes")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as exc:  # pragma: no cover - exercised in dependency-minimal wheels
            raise AssuranceDependencyError(
                "Ed25519Signer requires the 'cryptography' package"
            ) from exc
        return cls(
            key_id,
            cast(_PrivateKey, Ed25519PrivateKey.from_private_bytes(private_key)),
        )

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return "ed25519"

    @property
    def public_key_bytes(self) -> bytes:
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:  # pragma: no cover
            raise AssuranceDependencyError(
                "Ed25519Signer requires the 'cryptography' package"
            ) from exc
        public_key = self._private_key.public_key()
        public_bytes = getattr(public_key, "public_bytes", None)
        if not callable(public_bytes):  # pragma: no cover - defensive adapter contract
            raise AssuranceDependencyError("Ed25519 implementation cannot export a public key")
        result = public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not isinstance(result, bytes):  # pragma: no cover
            raise AssuranceDependencyError("Ed25519 implementation returned a non-byte public key")
        return result

    def sign(self, message: bytes) -> bytes:
        if not isinstance(message, bytes):
            raise AssuranceValidationError("signing input must be exact bytes")
        return self._private_key.sign(message)


@dataclass(frozen=True)
class StaticKeyProvider:
    """Explicit immutable key set for offline verification and tests."""

    keys: Mapping[tuple[str, str], bytes]

    def __post_init__(self) -> None:
        normalized: dict[tuple[str, str], bytes] = {}
        for (key_id, algorithm), public_key in self.keys.items():
            require_string(key_id, "key_id")
            require_string(algorithm, "algorithm")
            if not isinstance(public_key, bytes):
                raise AssuranceValidationError("verification public keys must be bytes")
            normalized[(key_id, algorithm)] = bytes(public_key)
        object.__setattr__(self, "keys", MappingProxyType(normalized))

    @classmethod
    def from_signers(cls, *signers: Ed25519Signer) -> StaticKeyProvider:
        return cls(
            {(signer.key_id, signer.algorithm): signer.public_key_bytes for signer in signers}
        )

    def public_key(self, key_id: str, algorithm: str) -> bytes | None:
        return self.keys.get((key_id, algorithm))


def _pae(payload_type: str, payload: bytes) -> bytes:
    type_bytes = payload_type.encode("utf-8")
    return (
        b"DSSEv1 "
        + str(len(type_bytes)).encode("ascii")
        + b" "
        + type_bytes
        + b" "
        + str(len(payload)).encode("ascii")
        + b" "
        + payload
    )


def _sign_envelope(payload_type: str, payload: bytes, signer: Signer | None) -> bytes:
    require_string(payload_type, "payload_type")
    if signer is None:
        raise AssuranceValidationError("an explicit signer is required; no fallback key exists")
    if not isinstance(payload, bytes):
        raise AssuranceValidationError("signed payload must be exact bytes")
    key_id = require_string(signer.key_id, "signer.key_id")
    algorithm = require_string(signer.algorithm, "signer.algorithm")
    signature = signer.sign(_pae(payload_type, payload))
    if not isinstance(signature, bytes) or not signature:
        raise AssuranceValidationError("signer must return non-empty signature bytes")
    return encode_profile_v1(
        {
            "schema_id": DSSE_ENVELOPE_SCHEMA_ID,
            "payloadType": payload_type,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": [
                {
                    "keyid": key_id,
                    "alg": algorithm,
                    "sig": base64.b64encode(signature).decode("ascii"),
                }
            ],
        }
    )


def sign_attestation(payload_type: str, payload: bytes, signer: Signer | None) -> bytes:
    """Sign arbitrary exact bytes with an authenticated, domain-separated payload type."""
    return _sign_envelope(payload_type, payload, signer)


class _UntrustedSigner(AssuranceVerificationError):
    pass


class _InvalidSignature(AssuranceVerificationError):
    pass


@dataclass(frozen=True)
class VerifiedEnvelope:
    payload_type: str
    payload: bytes
    verified_key_ids: tuple[str, ...]


def _verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover
        raise AssuranceDependencyError(
            "Ed25519 verification requires the 'cryptography' package"
        ) from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True


def _verify_envelope(data: bytes, keys: VerificationKeyProvider) -> VerifiedEnvelope:
    try:
        payload = decode_profile_v1(data)
    except AssuranceValidationError as exc:
        raise _InvalidSignature(f"invalid signed envelope: {exc}") from exc
    require_exact_fields(
        payload,
        frozenset({"schema_id", "payloadType", "payload", "signatures"}),
        "signed envelope",
    )
    if payload["schema_id"] != DSSE_ENVELOPE_SCHEMA_ID:
        raise _InvalidSignature("unsupported signed envelope schema")
    payload_type = require_string(payload["payloadType"], "payloadType")
    try:
        exact_payload = base64.b64decode(
            require_string(payload["payload"], "payload"), validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise _InvalidSignature("invalid signed-envelope payload encoding") from exc
    raw_signatures = payload["signatures"]
    if not isinstance(raw_signatures, (list, tuple)) or not raw_signatures:
        raise _InvalidSignature("signed envelope contains no signatures")
    verified: list[str] = []
    trusted_candidate_seen = False
    for item in raw_signatures:
        if not isinstance(item, Mapping):
            raise _InvalidSignature("signed-envelope signature entry must be an object")
        require_exact_fields(item, frozenset({"keyid", "alg", "sig"}), "signed-envelope signature")
        key_id = require_string(item["keyid"], "keyid")
        algorithm = require_string(item["alg"], "alg")
        public_key = keys.public_key(key_id, algorithm)
        if public_key is None:
            continue
        trusted_candidate_seen = True
        try:
            signature = base64.b64decode(require_string(item["sig"], "sig"), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _InvalidSignature("invalid signature encoding") from exc
        if algorithm != "ed25519":
            continue
        if _verify_ed25519(public_key, _pae(payload_type, exact_payload), signature):
            verified.append(key_id)
    unique_verified = tuple(dict.fromkeys(verified))
    if not unique_verified:
        if not trusted_candidate_seen:
            raise _UntrustedSigner("no signature resolves to an explicitly trusted key")
        raise _InvalidSignature("no trusted signature validates the exact payload bytes")
    return VerifiedEnvelope(payload_type, exact_payload, unique_verified)


def verify_attestation(
    envelope: bytes,
    *,
    expected_payload_type: str,
    keys: VerificationKeyProvider,
) -> VerifiedEnvelope:
    """Verify exact signed bytes and require the caller's expected payload type."""
    expected = require_string(expected_payload_type, "expected_payload_type")
    verified = _verify_envelope(envelope, keys)
    if verified.payload_type != expected:
        raise AssuranceVerificationError(
            f"attestation payload type {verified.payload_type!r} does not match {expected!r}"
        )
    return verified


@dataclass(frozen=True)
class ReceiptStatementV1:
    """Signed event metadata binding one exact logical decision."""

    decision_digest: str
    receipt_id: str
    issued_at: str
    sequence: int
    deployment_id: str
    transport_binding_digest: str
    stream_id: str
    previous_receipt_digest: str | None

    def __post_init__(self) -> None:
        for field_name in ("decision_digest", "transport_binding_digest"):
            object.__setattr__(
                self, field_name, require_digest(getattr(self, field_name), field_name)
            )
        if self.previous_receipt_digest is not None:
            object.__setattr__(
                self,
                "previous_receipt_digest",
                require_digest(self.previous_receipt_digest, "previous_receipt_digest"),
            )
        for field_name in ("receipt_id", "deployment_id", "stream_id", "issued_at"):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        parse_utc_second(self.issued_at, "issued_at")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise AssuranceValidationError("sequence must be a non-negative integer")

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": RECEIPT_SCHEMA_ID,
                "decision_digest": self.decision_digest,
                "receipt_id": self.receipt_id,
                "issued_at": self.issued_at,
                "sequence": self.sequence,
                "deployment_id": self.deployment_id,
                "transport_binding_digest": self.transport_binding_digest,
                "stream_id": self.stream_id,
                "previous_receipt_digest": self.previous_receipt_digest,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ReceiptStatementV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "decision_digest",
                "receipt_id",
                "issued_at",
                "sequence",
                "deployment_id",
                "transport_binding_digest",
                "stream_id",
                "previous_receipt_digest",
            }
        )
        require_exact_fields(payload, fields, "ReceiptStatementV1")
        if payload["schema_id"] != RECEIPT_SCHEMA_ID:
            raise AssuranceValidationError("unsupported receipt statement schema")
        sequence = payload["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise AssuranceValidationError("receipt sequence must be an integer")
        previous = payload["previous_receipt_digest"]
        if previous is not None and not isinstance(previous, str):
            raise AssuranceValidationError("previous_receipt_digest must be a digest or null")
        return cls(
            decision_digest=require_digest(payload["decision_digest"], "decision_digest"),
            receipt_id=require_string(payload["receipt_id"], "receipt_id"),
            issued_at=require_string(payload["issued_at"], "issued_at"),
            sequence=sequence,
            deployment_id=require_string(payload["deployment_id"], "deployment_id"),
            transport_binding_digest=require_digest(
                payload["transport_binding_digest"], "transport_binding_digest"
            ),
            stream_id=require_string(payload["stream_id"], "stream_id"),
            previous_receipt_digest=(
                require_digest(previous, "previous_receipt_digest")
                if previous is not None
                else None
            ),
        )


def sign_receipt(statement: ReceiptStatementV1, signer: Signer | None) -> bytes:
    """Sign the statement's exact canonical bytes using a required explicit signer."""
    return _sign_envelope(RECEIPT_PAYLOAD_TYPE, statement.to_bytes(), signer)
