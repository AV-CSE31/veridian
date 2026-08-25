"""Authenticated settlement receipts and banking postcondition verification."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from veridian.assurance import (
    AssuranceError,
    ClauseStatus,
    CompletionSemanticsV1,
    Signer,
    VerificationKeyProvider,
    decode_profile_v1,
    encode_profile_v1,
    sha256_digest,
    sign_attestation,
    verify_attestation,
)
from veridian.assurance._canonical import (
    require_digest,
    require_exact_fields,
)
from veridian.assurance._model import parse_utc_second

from ._errors import BankingPostconditionError, BankingValidationError
from ._models import BankPaymentIntentV1, _currency, _nonnegative_int, _positive_int, _string

BANK_SETTLEMENT_SCHEMA_ID = "veridian.bank-settlement-receipt.v1"
BANK_SETTLEMENT_PAYLOAD_TYPE = "application/vnd.veridian.bank-settlement-receipt.v1+json"


class BankJournalDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class BankSettlementStatus(StrEnum):
    ACCEPTED = "accepted"
    SETTLED = "settled"
    RETURNED = "returned"
    REVERSED = "reversed"
    FAILED = "failed"


def _digest(value: object, field_name: str) -> str:
    try:
        return require_digest(value, field_name)
    except Exception as exc:
        raise BankingValidationError(str(exc)) from exc


@dataclass(frozen=True)
class BankJournalLegV1:
    """One exact debit or credit in integer minor units."""

    account_id: str
    direction: BankJournalDirection
    amount_minor: int
    currency: str
    posting_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _string(self.account_id, "account_id"))
        object.__setattr__(self, "amount_minor", _positive_int(self.amount_minor, "amount_minor"))
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "posting_code", _string(self.posting_code, "posting_code"))
        if not isinstance(self.direction, BankJournalDirection):
            try:
                object.__setattr__(
                    self,
                    "direction",
                    BankJournalDirection(_string(self.direction, "direction")),
                )
            except ValueError as exc:
                raise BankingValidationError("unsupported journal direction") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "direction": self.direction.value,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "posting_code": self.posting_code,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> BankJournalLegV1:
        fields = frozenset({"account_id", "direction", "amount_minor", "currency", "posting_code"})
        try:
            require_exact_fields(payload, fields, "BankJournalLegV1")
            direction = BankJournalDirection(_string(payload["direction"], "direction"))
        except ValueError as exc:
            raise BankingValidationError("unsupported journal direction") from exc
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        return cls(
            account_id=_string(payload["account_id"], "account_id"),
            direction=direction,
            amount_minor=_positive_int(payload["amount_minor"], "amount_minor"),
            currency=_currency(payload["currency"]),
            posting_code=_string(payload["posting_code"], "posting_code"),
        )


@dataclass(frozen=True)
class BankSettlementReceiptV1:
    """Authoritative ledger/scheme result bound to an exact permit and intent."""

    settlement_id: str
    payment_id: str
    semantic_digest: str
    permit_digest: str
    idempotency_key: str
    status: BankSettlementStatus
    producer_id: str
    scheme_reference_digest: str
    ledger_version_before: int
    ledger_version_after: int
    journal: tuple[BankJournalLegV1, ...]
    observed_at: str

    def __post_init__(self) -> None:
        for field_name in ("settlement_id", "payment_id", "idempotency_key", "producer_id"):
            object.__setattr__(self, field_name, _string(getattr(self, field_name), field_name))
        for field_name in ("semantic_digest", "permit_digest", "scheme_reference_digest"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        if not isinstance(self.status, BankSettlementStatus):
            try:
                object.__setattr__(
                    self,
                    "status",
                    BankSettlementStatus(_string(self.status, "status")),
                )
            except ValueError as exc:
                raise BankingValidationError("unsupported settlement status") from exc
        object.__setattr__(
            self,
            "ledger_version_before",
            _nonnegative_int(self.ledger_version_before, "ledger_version_before"),
        )
        object.__setattr__(
            self,
            "ledger_version_after",
            _nonnegative_int(self.ledger_version_after, "ledger_version_after"),
        )
        journal = tuple(self.journal)
        if not journal or not all(isinstance(item, BankJournalLegV1) for item in journal):
            raise BankingValidationError("journal must contain BankJournalLegV1 values")
        object.__setattr__(self, "journal", journal)
        try:
            parse_utc_second(self.observed_at, "observed_at")
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": BANK_SETTLEMENT_SCHEMA_ID,
                "settlement_id": self.settlement_id,
                "payment_id": self.payment_id,
                "semantic_digest": self.semantic_digest,
                "permit_digest": self.permit_digest,
                "idempotency_key": self.idempotency_key,
                "status": self.status.value,
                "producer_id": self.producer_id,
                "scheme_reference_digest": self.scheme_reference_digest,
                "ledger_version_before": self.ledger_version_before,
                "ledger_version_after": self.ledger_version_after,
                "journal": [item.to_dict() for item in self.journal],
                "observed_at": self.observed_at,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> BankSettlementReceiptV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "settlement_id",
                "payment_id",
                "semantic_digest",
                "permit_digest",
                "idempotency_key",
                "status",
                "producer_id",
                "scheme_reference_digest",
                "ledger_version_before",
                "ledger_version_after",
                "journal",
                "observed_at",
            }
        )
        try:
            require_exact_fields(payload, fields, "BankSettlementReceiptV1")
            status = BankSettlementStatus(_string(payload["status"], "status"))
        except ValueError as exc:
            raise BankingValidationError("unsupported settlement status") from exc
        except Exception as exc:
            raise BankingValidationError(str(exc)) from exc
        raw_journal = payload["journal"]
        if not isinstance(raw_journal, (list, tuple)):
            raise BankingValidationError("journal must be an array")
        journal: list[BankJournalLegV1] = []
        for item in raw_journal:
            if not isinstance(item, Mapping):
                raise BankingValidationError("each journal leg must be an object")
            journal.append(BankJournalLegV1.from_dict(cast(Mapping[str, object], item)))
        if payload["schema_id"] != BANK_SETTLEMENT_SCHEMA_ID:
            raise BankingValidationError("unsupported bank settlement schema")
        return cls(
            settlement_id=_string(payload["settlement_id"], "settlement_id"),
            payment_id=_string(payload["payment_id"], "payment_id"),
            semantic_digest=_digest(payload["semantic_digest"], "semantic_digest"),
            permit_digest=_digest(payload["permit_digest"], "permit_digest"),
            idempotency_key=_string(payload["idempotency_key"], "idempotency_key"),
            status=status,
            producer_id=_string(payload["producer_id"], "producer_id"),
            scheme_reference_digest=_digest(
                payload["scheme_reference_digest"], "scheme_reference_digest"
            ),
            ledger_version_before=_nonnegative_int(
                payload["ledger_version_before"], "ledger_version_before"
            ),
            ledger_version_after=_nonnegative_int(
                payload["ledger_version_after"], "ledger_version_after"
            ),
            journal=tuple(journal),
            observed_at=_string(payload["observed_at"], "observed_at"),
        )


def sign_bank_settlement(receipt: BankSettlementReceiptV1, signer: Signer | None) -> bytes:
    if not isinstance(receipt, BankSettlementReceiptV1):
        raise BankingValidationError("sign_bank_settlement requires BankSettlementReceiptV1")
    try:
        return sign_attestation(BANK_SETTLEMENT_PAYLOAD_TYPE, receipt.to_bytes(), signer)
    except AssuranceError as exc:
        raise BankingValidationError(f"bank settlement attestation failed: {exc}") from exc


@dataclass(frozen=True)
class BankPostconditionResult:
    status: ClauseStatus
    reason_codes: tuple[str, ...]
    receipt: BankSettlementReceiptV1
    completion: CompletionSemanticsV1 | None
    verified_key_ids: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return self.status is ClauseStatus.SATISFIED


def verify_bank_settlement(
    signed_receipt: bytes,
    *,
    keys: VerificationKeyProvider,
    intent: BankPaymentIntentV1,
    expected_permit_digest: str,
) -> BankPostconditionResult:
    """Verify authentic settlement, conservation, exact posting, and state advance."""

    try:
        expected_permit_digest = require_digest(expected_permit_digest, "expected_permit_digest")
        verified = verify_attestation(
            signed_receipt,
            expected_payload_type=BANK_SETTLEMENT_PAYLOAD_TYPE,
            keys=keys,
        )
        receipt = BankSettlementReceiptV1.from_bytes(verified.payload)
    except (AssuranceError, BankingValidationError) as exc:
        raise BankingPostconditionError(f"bank settlement verification failed: {exc}") from exc
    if receipt.payment_id != intent.payment_id:
        raise BankingPostconditionError("settlement payment ID does not match intent")
    if receipt.semantic_digest != intent.digest:
        raise BankingPostconditionError("settlement semantic digest does not match intent")
    if receipt.permit_digest != expected_permit_digest:
        raise BankingPostconditionError("settlement permit digest does not match")

    reasons: list[str] = ["BANK_SETTLEMENT_BOUND"]
    violated = False
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for leg in receipt.journal:
        index = 0 if leg.direction is BankJournalDirection.DEBIT else 1
        totals[leg.currency][index] += leg.amount_minor
    balanced = all(debits == credits for debits, credits in totals.values())
    reasons.append("BANK_JOURNAL_BALANCED" if balanced else "BANK_JOURNAL_UNBALANCED")
    violated |= not balanced

    debtor_matches = any(
        leg.account_id == intent.debtor_account_id
        and leg.direction is BankJournalDirection.DEBIT
        and leg.amount_minor == intent.amount_minor + intent.fee_minor
        and leg.currency == intent.currency
        for leg in receipt.journal
    )
    creditor_matches = any(
        leg.account_id == intent.creditor_account_id
        and leg.direction is BankJournalDirection.CREDIT
        and leg.amount_minor == intent.amount_minor
        and leg.currency == intent.currency
        for leg in receipt.journal
    )
    fee_matches = intent.fee_minor == 0 or any(
        leg.direction is BankJournalDirection.CREDIT
        and leg.amount_minor == intent.fee_minor
        and leg.currency == intent.currency
        and leg.posting_code == "PAYMENT_FEE"
        for leg in receipt.journal
    )
    posting_matches = debtor_matches and creditor_matches and fee_matches
    reasons.append("BANK_POSTING_MATCHED" if posting_matches else "BANK_POSTING_MISMATCH")
    violated |= not posting_matches

    version_advanced = receipt.ledger_version_after > receipt.ledger_version_before
    reasons.append(
        "BANK_LEDGER_VERSION_ADVANCED" if version_advanced else "BANK_LEDGER_VERSION_STALE"
    )
    violated |= not version_advanced

    completion: CompletionSemanticsV1 | None = None
    if receipt.status is BankSettlementStatus.SETTLED:
        reasons.append("BANK_SETTLED")
        status = ClauseStatus.VIOLATED if violated else ClauseStatus.SATISFIED
        if not violated:
            completion = CompletionSemanticsV1(
                completion_type="bank.payment.settled",
                subject=f"payment:{intent.payment_id}",
                assertions={
                    "semantic_digest": intent.digest,
                    "permit_digest": expected_permit_digest,
                    "settlement_receipt_digest": receipt.digest,
                    "ledger_version": receipt.ledger_version_after,
                },
            )
    elif receipt.status is BankSettlementStatus.ACCEPTED:
        reasons.append("BANK_SETTLEMENT_PENDING")
        status = ClauseStatus.VIOLATED if violated else ClauseStatus.UNKNOWN
    else:
        reasons.append(f"BANK_SETTLEMENT_{receipt.status.value.upper()}")
        status = ClauseStatus.VIOLATED
    return BankPostconditionResult(
        status=status,
        reason_codes=tuple(reasons),
        receipt=receipt,
        completion=completion,
        verified_key_ids=verified.verified_key_ids,
    )
