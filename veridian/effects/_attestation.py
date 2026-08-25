"""Exact-byte attestations for permits and effect receipts."""

from __future__ import annotations

from dataclasses import dataclass

from veridian.assurance import (
    ActionSemanticsV1,
    AssuranceError,
    Signer,
    VerificationKeyProvider,
    sign_attestation,
    verify_attestation,
)
from veridian.assurance._canonical import require_digest, require_string
from veridian.assurance._model import parse_utc_second

from ._errors import EffectValidationError, PermitError
from ._permit import ExecutionPermitV1
from ._receipt import EFFECT_RECEIPT_PAYLOAD_TYPE, EffectReceiptV1

EXECUTION_PERMIT_PAYLOAD_TYPE = "application/vnd.veridian.execution-permit.v1+json"


@dataclass(frozen=True)
class VerifiedExecutionPermit:
    """A permit whose signature and runtime context were checked."""

    permit: ExecutionPermitV1
    verified_key_ids: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedEffectReceipt:
    """An effect receipt whose exact bytes have a trusted signature."""

    receipt: EffectReceiptV1
    verified_key_ids: tuple[str, ...]


def sign_execution_permit(permit: ExecutionPermitV1, signer: Signer | None) -> bytes:
    """Sign one exact permit payload; no implicit signer or key is available."""

    if not isinstance(permit, ExecutionPermitV1):
        raise PermitError("sign_execution_permit requires an ExecutionPermitV1")
    try:
        return sign_attestation(EXECUTION_PERMIT_PAYLOAD_TYPE, permit.to_bytes(), signer)
    except AssuranceError as exc:
        raise PermitError(f"permit attestation failed: {exc}") from exc


def verify_execution_permit(
    envelope: bytes,
    *,
    keys: VerificationKeyProvider,
    semantics: ActionSemanticsV1,
    expected_audience: str,
    current_state_digest: str,
    current_policy_digest: str,
    verified_at: str,
) -> VerifiedExecutionPermit:
    """Verify a signed permit and all volatile executor-side bindings."""

    if not isinstance(semantics, ActionSemanticsV1):
        raise PermitError("execution permit requires ActionSemanticsV1")
    try:
        expected_audience = require_string(expected_audience, "expected_audience")
        current_state_digest = require_digest(current_state_digest, "current_state_digest")
        current_policy_digest = require_digest(current_policy_digest, "current_policy_digest")
        now = parse_utc_second(verified_at, "verified_at")
        verified = verify_attestation(
            envelope,
            expected_payload_type=EXECUTION_PERMIT_PAYLOAD_TYPE,
            keys=keys,
        )
    except AssuranceError as exc:
        raise PermitError(f"permit attestation verification failed: {exc}") from exc
    except Exception as exc:
        raise PermitError(str(exc)) from exc
    try:
        permit = ExecutionPermitV1.from_bytes(verified.payload)
    except PermitError as exc:
        raise PermitError(f"attested permit payload is invalid: {exc}") from exc
    if permit.semantic_digest != semantics.digest:
        raise PermitError("permit semantic digest does not match the exact action")
    if permit.audience != expected_audience:
        raise PermitError("permit audience does not match this executor")
    if permit.state_digest != current_state_digest:
        raise PermitError("permit state is stale")
    if permit.policy_digest != current_policy_digest:
        raise PermitError("permit policy is stale")
    start = parse_utc_second(permit.not_before, "permit.not_before")
    end = parse_utc_second(permit.expires_at, "permit.expires_at")
    if now < start:
        raise PermitError("permit is not yet valid")
    if now >= end:
        raise PermitError("permit has expired")
    return VerifiedExecutionPermit(permit, verified.verified_key_ids)


def sign_effect_receipt(receipt: EffectReceiptV1, signer: Signer | None) -> bytes:
    """Sign one exact effect-receipt statement."""

    if not isinstance(receipt, EffectReceiptV1):
        raise EffectValidationError("sign_effect_receipt requires an EffectReceiptV1")
    try:
        return sign_attestation(EFFECT_RECEIPT_PAYLOAD_TYPE, receipt.to_bytes(), signer)
    except AssuranceError as exc:
        raise EffectValidationError(f"effect receipt attestation failed: {exc}") from exc


def verify_effect_receipt(
    envelope: bytes,
    *,
    keys: VerificationKeyProvider,
) -> VerifiedEffectReceipt:
    """Verify and parse one exact effect-receipt attestation."""

    try:
        verified = verify_attestation(
            envelope,
            expected_payload_type=EFFECT_RECEIPT_PAYLOAD_TYPE,
            keys=keys,
        )
    except AssuranceError as exc:
        raise EffectValidationError(
            f"effect receipt attestation verification failed: {exc}"
        ) from exc
    return VerifiedEffectReceipt(
        EffectReceiptV1.from_bytes(verified.payload), verified.verified_key_ids
    )
