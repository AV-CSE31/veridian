from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.assurance import ClauseStatus, Ed25519Signer, StaticKeyProvider
from veridian.banking import (
    BankingPostconditionError,
    BankJournalDirection,
    BankJournalLegV1,
    BankPaymentIntentV1,
    BankSettlementReceiptV1,
    BankSettlementStatus,
    sign_bank_settlement,
    verify_bank_settlement,
)

_SEED = bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")
_PERMIT = "sha256:" + "7" * 64


def _intent() -> BankPaymentIntentV1:
    return BankPaymentIntentV1(
        payment_id="PAY-2026-0009001",
        debtor_account_id="account:treasury-usd-001",
        creditor_account_id="account:acme-usd-042",
        beneficiary_id="beneficiary:acme-industrial",
        amount_minor=1_250_000_000,
        fee_minor=2_500,
        currency="USD",
        value_date="2026-08-19",
        rail="RTGS",
        purpose="invoice:INV-314",
    )


def _receipt(intent: BankPaymentIntentV1) -> BankSettlementReceiptV1:
    return BankSettlementReceiptV1(
        settlement_id="settlement-RTGS-9001",
        payment_id=intent.payment_id,
        semantic_digest=intent.digest,
        permit_digest=_PERMIT,
        idempotency_key="payment-PAY-9001",
        status=BankSettlementStatus.SETTLED,
        producer_id="bank-ledger:prod",
        scheme_reference_digest="sha256:" + "4" * 64,
        ledger_version_before=991_004,
        ledger_version_after=991_005,
        journal=(
            BankJournalLegV1(
                account_id=intent.debtor_account_id,
                direction=BankJournalDirection.DEBIT,
                amount_minor=intent.amount_minor + intent.fee_minor,
                currency="USD",
                posting_code="CLIENT_PAYMENT",
            ),
            BankJournalLegV1(
                account_id=intent.creditor_account_id,
                direction=BankJournalDirection.CREDIT,
                amount_minor=intent.amount_minor,
                currency="USD",
                posting_code="BENEFICIARY_CREDIT",
            ),
            BankJournalLegV1(
                account_id="account:fee-income-usd",
                direction=BankJournalDirection.CREDIT,
                amount_minor=intent.fee_minor,
                currency="USD",
                posting_code="PAYMENT_FEE",
            ),
        ),
        observed_at="2026-08-19T10:00:14Z",
    )


def test_signed_settlement_verifies_double_entry_and_exact_payment_binding() -> None:
    intent = _intent()
    receipt = _receipt(intent)
    signer = Ed25519Signer.from_private_bytes("bank-ledger-key", _SEED)

    result = verify_bank_settlement(
        sign_bank_settlement(receipt, signer),
        keys=StaticKeyProvider.from_signers(signer),
        intent=intent,
        expected_permit_digest=_PERMIT,
    )

    assert result.status is ClauseStatus.SATISFIED
    assert result.reason_codes == (
        "BANK_SETTLEMENT_BOUND",
        "BANK_JOURNAL_BALANCED",
        "BANK_POSTING_MATCHED",
        "BANK_LEDGER_VERSION_ADVANCED",
        "BANK_SETTLED",
    )
    assert result.completion.subject == f"payment:{intent.payment_id}"


def test_unbalanced_or_wrong_posting_never_becomes_completion() -> None:
    intent = _intent()
    receipt = _receipt(intent)
    signer = Ed25519Signer.from_private_bytes("bank-ledger-key", _SEED)
    bad_fee = replace(receipt.journal[2], amount_minor=intent.fee_minor - 1)
    unbalanced = replace(receipt, journal=(*receipt.journal[:2], bad_fee))

    result = verify_bank_settlement(
        sign_bank_settlement(unbalanced, signer),
        keys=StaticKeyProvider.from_signers(signer),
        intent=intent,
        expected_permit_digest=_PERMIT,
    )

    assert result.status is ClauseStatus.VIOLATED
    assert "BANK_JOURNAL_UNBALANCED" in result.reason_codes


def test_receipt_substitution_is_a_binding_error_not_a_soft_failure() -> None:
    intent = _intent()
    signer = Ed25519Signer.from_private_bytes("bank-ledger-key", _SEED)
    substituted = replace(_receipt(intent), semantic_digest="sha256:" + "0" * 64)

    with pytest.raises(BankingPostconditionError, match="semantic"):
        verify_bank_settlement(
            sign_bank_settlement(substituted, signer),
            keys=StaticKeyProvider.from_signers(signer),
            intent=intent,
            expected_permit_digest=_PERMIT,
        )


def test_scheme_acknowledgement_is_not_misreported_as_settlement() -> None:
    intent = _intent()
    signer = Ed25519Signer.from_private_bytes("bank-ledger-key", _SEED)
    accepted = replace(_receipt(intent), status=BankSettlementStatus.ACCEPTED)

    result = verify_bank_settlement(
        sign_bank_settlement(accepted, signer),
        keys=StaticKeyProvider.from_signers(signer),
        intent=intent,
        expected_permit_digest=_PERMIT,
    )

    assert result.status is ClauseStatus.UNKNOWN
    assert "BANK_SETTLEMENT_PENDING" in result.reason_codes
