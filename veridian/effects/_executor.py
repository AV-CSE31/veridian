"""Trusted executor that keeps credentials behind a signed permit boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from veridian.assurance import (
    ActionSemanticsV1,
    Signer,
    VerificationKeyProvider,
    sha256_digest,
)
from veridian.assurance._canonical import require_digest, require_string
from veridian.assurance._model import parse_utc_second

from ._attestation import (
    sign_effect_receipt,
    verify_effect_receipt,
    verify_execution_permit,
)
from ._errors import EffectExecutionError, EffectValidationError
from ._permit import ExecutionPermitV1
from ._receipt import EffectReceiptType, EffectReceiptV1
from ._store import OutboxRecord, OutboxStatus, SqlitePermitStore


@dataclass(frozen=True)
class DispatchRequest:
    """Narrow request passed to a credential-holding effect adapter."""

    outbox_id: str
    idempotency_key: str
    payload: bytes
    payload_digest: str
    semantic_digest: str
    permit_digest: str

    def __post_init__(self) -> None:
        try:
            require_string(self.outbox_id, "outbox_id")
            require_string(self.idempotency_key, "idempotency_key")
            for field_name in ("payload_digest", "semantic_digest", "permit_digest"):
                require_digest(getattr(self, field_name), field_name)
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        if not isinstance(self.payload, bytes) or not self.payload:
            raise EffectValidationError("dispatch payload must be non-empty bytes")
        if sha256_digest(self.payload) != self.payload_digest:
            raise EffectValidationError("dispatch payload digest does not match exact bytes")


@dataclass(frozen=True)
class DispatchResult:
    """Stable result an idempotent adapter returns for one idempotency key."""

    producer_id: str
    receipt_type: EffectReceiptType
    observed_at: str
    external_reference_digest: str
    result_digest: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "producer_id", require_string(self.producer_id, "producer_id"))
            parse_utc_second(self.observed_at, "observed_at")
            object.__setattr__(
                self,
                "external_reference_digest",
                require_digest(self.external_reference_digest, "external_reference_digest"),
            )
            object.__setattr__(
                self, "result_digest", require_digest(self.result_digest, "result_digest")
            )
        except Exception as exc:
            raise EffectValidationError(str(exc)) from exc
        if not isinstance(self.receipt_type, EffectReceiptType):
            raise EffectValidationError("receipt_type must be an EffectReceiptType")


class EffectAdapter(Protocol):
    """Credential-holding adapter; retries must honor the idempotency key."""

    @property
    def adapter_id(self) -> str: ...

    def dispatch(self, request: DispatchRequest) -> DispatchResult: ...


@dataclass(frozen=True)
class ExecutionOutcome:
    """Authenticated result returned by the trusted executor."""

    permit: ExecutionPermitV1
    outbox: OutboxRecord
    receipt: EffectReceiptV1
    receipt_envelope: bytes
    verified_permit_key_ids: tuple[str, ...]
    replayed: bool


class TrustedExecutor:
    """Verify, atomically consume, dispatch, and attest one exact action."""

    def __init__(
        self,
        *,
        audience: str,
        store: SqlitePermitStore,
        permit_keys: VerificationKeyProvider,
        receipt_keys: VerificationKeyProvider,
        receipt_signer: Signer,
        adapter: EffectAdapter,
    ) -> None:
        try:
            self._audience = require_string(audience, "audience")
            require_string(adapter.adapter_id, "adapter.adapter_id")
            require_string(receipt_signer.key_id, "receipt_signer.key_id")
            require_string(receipt_signer.algorithm, "receipt_signer.algorithm")
        except Exception as exc:
            raise EffectExecutionError(str(exc)) from exc
        if not isinstance(store, SqlitePermitStore):
            raise EffectExecutionError("store must be a SqlitePermitStore")
        if receipt_keys.public_key(receipt_signer.key_id, receipt_signer.algorithm) is None:
            raise EffectExecutionError("receipt signer is absent from receipt verification keys")
        self._store = store
        self._permit_keys = permit_keys
        self._receipt_keys = receipt_keys
        self._receipt_signer = receipt_signer
        self._adapter = adapter

    @staticmethod
    def _effect_id(permit: ExecutionPermitV1) -> str:
        return "eff_" + permit.digest.removeprefix("sha256:")[:32]

    @staticmethod
    def _receipt_id(outbox: OutboxRecord, result: DispatchResult) -> str:
        identity = sha256_digest(
            f"{outbox.outbox_id}\n{result.result_digest}\n{result.receipt_type.value}".encode()
        )
        return "er_" + identity.removeprefix("sha256:")[:32]

    def _replay_outcome(
        self,
        *,
        permit: ExecutionPermitV1,
        outbox: OutboxRecord,
        verified_key_ids: tuple[str, ...],
    ) -> ExecutionOutcome:
        if outbox.receipt_envelope is None:
            raise EffectExecutionError("dispatched outbox is missing its effect receipt")
        try:
            verified = verify_effect_receipt(outbox.receipt_envelope, keys=self._receipt_keys)
        except EffectValidationError as exc:
            raise EffectExecutionError(f"stored effect receipt is invalid: {exc}") from exc
        receipt = verified.receipt
        if (
            receipt.effect_id != self._effect_id(permit)
            or receipt.semantic_digest != permit.semantic_digest
            or receipt.authorization_envelope_digest != permit.authorization_envelope_digest
            or receipt.permit_digest != permit.digest
            or receipt.outbox_id != outbox.outbox_id
            or receipt.result_digest != outbox.response_digest
            or receipt.external_reference_digest != outbox.external_reference_digest
        ):
            raise EffectExecutionError("stored effect receipt does not bind the exact execution")
        return ExecutionOutcome(
            permit=permit,
            outbox=outbox,
            receipt=receipt,
            receipt_envelope=outbox.receipt_envelope,
            verified_permit_key_ids=verified_key_ids,
            replayed=True,
        )

    def execute(
        self,
        *,
        signed_permit: bytes,
        semantics: ActionSemanticsV1,
        current_state_digest: str,
        current_policy_digest: str,
        executed_at: str,
    ) -> ExecutionOutcome:
        """Execute only a trusted, current, exact permit; recover exact retries."""

        verified = verify_execution_permit(
            signed_permit,
            keys=self._permit_keys,
            semantics=semantics,
            expected_audience=self._audience,
            current_state_digest=current_state_digest,
            current_policy_digest=current_policy_digest,
            verified_at=executed_at,
        )
        permit = verified.permit
        self._store.register(permit)
        outbox = self._store.redeem(
            permit,
            audience=self._audience,
            current_state_digest=current_state_digest,
            current_policy_digest=current_policy_digest,
            dispatch_payload=semantics.to_bytes(),
            redeemed_at=executed_at,
        )
        if outbox.status is OutboxStatus.DISPATCHED:
            return self._replay_outcome(
                permit=permit,
                outbox=outbox,
                verified_key_ids=verified.verified_key_ids,
            )

        request = DispatchRequest(
            outbox_id=outbox.outbox_id,
            idempotency_key=outbox.idempotency_key,
            payload=outbox.dispatch_payload,
            payload_digest=outbox.payload_digest,
            semantic_digest=permit.semantic_digest,
            permit_digest=permit.digest,
        )
        try:
            result = self._adapter.dispatch(request)
        except Exception as exc:
            raise EffectExecutionError(
                f"adapter dispatch failed for durable outbox {outbox.outbox_id}: {exc}"
            ) from exc
        if not isinstance(result, DispatchResult):
            raise EffectExecutionError("effect adapter returned a non-DispatchResult")
        receipt = EffectReceiptV1(
            receipt_id=self._receipt_id(outbox, result),
            receipt_type=result.receipt_type,
            effect_id=self._effect_id(permit),
            semantic_digest=permit.semantic_digest,
            authorization_envelope_digest=permit.authorization_envelope_digest,
            permit_digest=permit.digest,
            outbox_id=outbox.outbox_id,
            producer_id=result.producer_id,
            observed_at=result.observed_at,
            external_reference_digest=result.external_reference_digest,
            result_digest=result.result_digest,
            previous_receipt_digest=None,
        )
        receipt_envelope = sign_effect_receipt(receipt, self._receipt_signer)
        completed = self._store.mark_dispatched(
            outbox.outbox_id,
            response_digest=result.result_digest,
            dispatched_at=result.observed_at,
            external_reference_digest=result.external_reference_digest,
            receipt_envelope=receipt_envelope,
        )
        return ExecutionOutcome(
            permit=permit,
            outbox=completed,
            receipt=receipt,
            receipt_envelope=receipt_envelope,
            verified_permit_key_ids=verified.verified_key_ids,
            replayed=False,
        )
