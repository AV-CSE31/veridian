from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.effects import EffectReceiptType, EffectReceiptV1, EffectValidationError

_SEMANTIC = "sha256:" + "1" * 64
_AUTHORIZATION = "sha256:" + "2" * 64
_PERMIT = "sha256:" + "3" * 64
_EXTERNAL_REFERENCE = "sha256:" + "4" * 64
_RESULT = "sha256:" + "5" * 64


def _receipt() -> EffectReceiptV1:
    return EffectReceiptV1(
        receipt_id="effect-receipt-0123456789abcdef",
        receipt_type=EffectReceiptType.COMMITTED,
        effect_id="eff_payment_9001",
        semantic_digest=_SEMANTIC,
        authorization_envelope_digest=_AUTHORIZATION,
        permit_digest=_PERMIT,
        outbox_id="out_0123456789abcdef0123456789abcdef",
        producer_id="bank-simulator:rtgs",
        observed_at="2026-08-19T10:00:14Z",
        external_reference_digest=_EXTERNAL_REFERENCE,
        result_digest=_RESULT,
        previous_receipt_digest=None,
    )


def test_effect_receipt_is_an_exact_protocol_neutral_postcondition_statement() -> None:
    receipt = _receipt()
    encoded = receipt.to_bytes()

    assert b"12_500_000" not in encoded
    assert b"merchant-42" not in encoded
    assert b"raw_payload" not in encoded
    assert EffectReceiptV1.from_bytes(encoded) == receipt


def test_all_security_critical_bindings_change_receipt_identity() -> None:
    receipt = _receipt()

    assert replace(receipt, semantic_digest="sha256:" + "a" * 64).digest != receipt.digest
    assert replace(receipt, permit_digest="sha256:" + "b" * 64).digest != receipt.digest
    assert replace(receipt, result_digest="sha256:" + "c" * 64).digest != receipt.digest


def test_effect_receipt_rejects_missing_or_malformed_exact_bindings() -> None:
    with pytest.raises(EffectValidationError):
        replace(_receipt(), outbox_id="")
    with pytest.raises(EffectValidationError):
        replace(_receipt(), external_reference_digest="not-a-digest")
