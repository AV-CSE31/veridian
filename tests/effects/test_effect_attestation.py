from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.assurance import (
    ActionSemanticsV1,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    Ed25519Signer,
    StaticKeyProvider,
)
from veridian.effects import (
    EffectReceiptType,
    EffectReceiptV1,
    ExecutionPermitV1,
    PermitError,
    sign_effect_receipt,
    sign_execution_permit,
    verify_effect_receipt,
    verify_execution_permit,
)

_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
_STATE = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_CONTRACT = "sha256:" + "c" * 64
_MANIFEST = "sha256:" + "7" * 64


def _objects() -> tuple[ActionSemanticsV1, ExecutionPermitV1, Ed25519Signer]:
    action = ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-42",
        {"amount_minor": 12_500_000, "currency": "USD"},
    )
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="authorization-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest=_STATE,
        policy_digest=_POLICY,
    )
    clause = ClauseResultV1(
        clause_id="bank-controls",
        severity=ClauseSeverity.HARD,
        status=ClauseStatus.SATISFIED,
        reason_code="BANK_CONTROLS_SATISFIED",
        verifier_manifest_digest=_MANIFEST,
        evidence_ids=("ev_0123456789abcdef",),
        details={},
    )
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=_CONTRACT,
        snapshot_digest=_STATE,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )
    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=decision,
        permit_id="permit_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )
    return action, permit, Ed25519Signer.from_private_bytes("permit-key-2026-08", _SEED)


def test_signed_permit_is_verified_against_exact_runtime_context() -> None:
    action, permit, signer = _objects()
    envelope = sign_execution_permit(permit, signer)

    verified = verify_execution_permit(
        envelope,
        keys=StaticKeyProvider.from_signers(signer),
        semantics=action,
        expected_audience="bank-executor:prod",
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        verified_at="2026-08-19T10:00:10Z",
    )

    assert verified.permit == permit
    assert verified.verified_key_ids == ("permit-key-2026-08",)


def test_signed_permit_rejects_semantic_substitution_and_byte_tampering() -> None:
    action, permit, signer = _objects()
    envelope = sign_execution_permit(permit, signer)
    changed_action = replace(
        action,
        parameters={"amount_minor": 12_500_001, "currency": "USD"},
    )

    with pytest.raises(PermitError, match="semantic"):
        verify_execution_permit(
            envelope,
            keys=StaticKeyProvider.from_signers(signer),
            semantics=changed_action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        )

    with pytest.raises(PermitError, match="attestation"):
        verify_execution_permit(
            envelope[:-1] + bytes([envelope[-1] ^ 1]),
            keys=StaticKeyProvider.from_signers(signer),
            semantics=action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        )


def test_effect_receipt_attestation_round_trips_exact_statement() -> None:
    _, permit, signer = _objects()
    receipt = EffectReceiptV1(
        receipt_id="effect-receipt-0123456789abcdef",
        receipt_type=EffectReceiptType.COMMITTED,
        effect_id="eff_payment_9001",
        semantic_digest=permit.semantic_digest,
        authorization_envelope_digest=permit.authorization_envelope_digest,
        permit_digest=permit.digest,
        outbox_id="out_0123456789abcdef0123456789abcdef",
        producer_id="bank-simulator:rtgs",
        observed_at="2026-08-19T10:00:14Z",
        external_reference_digest="sha256:" + "4" * 64,
        result_digest="sha256:" + "6" * 64,
        previous_receipt_digest=None,
    )

    verified = verify_effect_receipt(
        sign_effect_receipt(receipt, signer),
        keys=StaticKeyProvider.from_signers(signer),
    )

    assert verified.receipt == receipt
    assert verified.verified_key_ids == ("permit-key-2026-08",)
