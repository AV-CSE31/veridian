"""Deterministic idempotent RTGS simulator for the industrial showcase."""

from __future__ import annotations

import threading

from veridian.assurance import Signer, sha256_digest
from veridian.assurance._canonical import require_string
from veridian.assurance._model import parse_utc_second
from veridian.effects import DispatchRequest, DispatchResult, EffectReceiptType

from ._errors import BankingValidationError
from ._models import BankPaymentIntentV1, _nonnegative_int
from ._settlement import (
    BankJournalDirection,
    BankJournalLegV1,
    BankSettlementReceiptV1,
    BankSettlementStatus,
    sign_bank_settlement,
)


class SyntheticRtgsAdapter:
    """Credential-boundary simulator with stable idempotent settlement results.

    This is a conformance/reference adapter, not a connector to a live payment
    scheme. It deterministically models ledger posting and a settled RTGS result.
    """

    adapter_id = "veridian.synthetic-rtgs.v1"

    def __init__(
        self,
        *,
        settlement_signer: Signer,
        observed_at: str,
        starting_ledger_version: int,
        producer_id: str = "bank-ledger:synthetic-rtgs",
        fee_account_id: str = "account:fee-income-usd",
    ) -> None:
        try:
            require_string(settlement_signer.key_id, "settlement_signer.key_id")
            require_string(settlement_signer.algorithm, "settlement_signer.algorithm")
            parse_utc_second(observed_at, "observed_at")
            producer_id = require_string(producer_id, "producer_id")
            fee_account_id = require_string(fee_account_id, "fee_account_id")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        self._signer = settlement_signer
        self._observed_at = observed_at
        self._ledger_version = _nonnegative_int(starting_ledger_version, "starting_ledger_version")
        self._producer_id = producer_id
        self._fee_account_id = fee_account_id
        self._lock = threading.Lock()
        self._entries: dict[
            str,
            tuple[tuple[str, str, str], DispatchResult, bytes],
        ] = {}
        self._economic_effect_count = 0

    @property
    def economic_effect_count(self) -> int:
        with self._lock:
            return self._economic_effect_count

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        if not isinstance(request, DispatchRequest):
            raise BankingValidationError("RTGS adapter requires DispatchRequest")
        fingerprint = (request.payload_digest, request.semantic_digest, request.permit_digest)
        with self._lock:
            previous = self._entries.get(request.idempotency_key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise BankingValidationError(
                        "idempotency key was reused for a different payment binding"
                    )
                return previous[1]

            intent = BankPaymentIntentV1.from_bytes(request.payload)
            if intent.digest != request.semantic_digest:
                raise BankingValidationError(
                    "dispatch semantic digest does not match canonical bank payment"
                )
            before = self._ledger_version
            after = before + 1
            journal: list[BankJournalLegV1] = [
                BankJournalLegV1(
                    account_id=intent.debtor_account_id,
                    direction=BankJournalDirection.DEBIT,
                    amount_minor=intent.amount_minor + intent.fee_minor,
                    currency=intent.currency,
                    posting_code="CLIENT_PAYMENT",
                ),
                BankJournalLegV1(
                    account_id=intent.creditor_account_id,
                    direction=BankJournalDirection.CREDIT,
                    amount_minor=intent.amount_minor,
                    currency=intent.currency,
                    posting_code="BENEFICIARY_CREDIT",
                ),
            ]
            if intent.fee_minor:
                journal.append(
                    BankJournalLegV1(
                        account_id=self._fee_account_id,
                        direction=BankJournalDirection.CREDIT,
                        amount_minor=intent.fee_minor,
                        currency=intent.currency,
                        posting_code="PAYMENT_FEE",
                    )
                )
            reference_digest = sha256_digest(
                f"RTGS\n{request.idempotency_key}\n{intent.payment_id}".encode()
            )
            settlement_id = "settlement-" + reference_digest.removeprefix("sha256:")[:24]
            receipt = BankSettlementReceiptV1(
                settlement_id=settlement_id,
                payment_id=intent.payment_id,
                semantic_digest=intent.digest,
                permit_digest=request.permit_digest,
                idempotency_key=request.idempotency_key,
                status=BankSettlementStatus.SETTLED,
                producer_id=self._producer_id,
                scheme_reference_digest=reference_digest,
                ledger_version_before=before,
                ledger_version_after=after,
                journal=tuple(journal),
                observed_at=self._observed_at,
            )
            envelope = sign_bank_settlement(receipt, self._signer)
            result = DispatchResult(
                producer_id=self._producer_id,
                receipt_type=EffectReceiptType.COMMITTED,
                observed_at=self._observed_at,
                external_reference_digest=reference_digest,
                result_digest=receipt.digest,
            )
            self._entries[request.idempotency_key] = (fingerprint, result, envelope)
            self._ledger_version = after
            self._economic_effect_count += 1
            return result

    def settlement_envelope(self, idempotency_key: str) -> bytes:
        try:
            idempotency_key = require_string(idempotency_key, "idempotency_key")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        with self._lock:
            entry = self._entries.get(idempotency_key)
            if entry is None:
                raise BankingValidationError("no settlement exists for idempotency key")
            return entry[2]
