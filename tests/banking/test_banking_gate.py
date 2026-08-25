from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.assurance import (
    AuthorizationEnvelope,
    Disposition,
    Ed25519Signer,
    StaticKeyProvider,
    sha256_digest,
)
from veridian.banking import (
    BankApprovalV1,
    BankControlSnapshotV1,
    BankingGate,
    BankingValidationError,
    BankPaymentIntentV1,
    BankPolicyV1,
    sign_bank_snapshot,
)

_SEED = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")


def _policy() -> BankPolicyV1:
    return BankPolicyV1(
        policy_id="treasury-usd-rtgs",
        policy_version="2026.08.1",
        currency="USD",
        per_payment_limit_minor=2_000_000_000,
        rolling_limit_minor=10_000_000_000,
        liquidity_buffer_minor=100_000_000,
        high_value_threshold_minor=1_000_000_000,
        standard_approval_quorum=1,
        high_value_approval_quorum=2,
        eligible_approval_roles=("maker", "checker"),
        allowed_beneficiaries=("beneficiary:acme-industrial",),
    )


def _intent(amount_minor: int = 1_250_000_000) -> BankPaymentIntentV1:
    return BankPaymentIntentV1(
        payment_id="PAY-2026-0009001",
        debtor_account_id="account:treasury-usd-001",
        creditor_account_id="account:acme-usd-042",
        beneficiary_id="beneficiary:acme-industrial",
        amount_minor=amount_minor,
        fee_minor=2_500,
        currency="USD",
        value_date="2026-08-19",
        rail="RTGS",
        purpose="invoice:INV-314",
    )


def _snapshot(intent: BankPaymentIntentV1) -> BankControlSnapshotV1:
    return BankControlSnapshotV1(
        evidence_id="ev_bank_controls_0123456789abcdef",
        producer_id="bank-controls:prod",
        account_id=intent.debtor_account_id,
        ledger_version=991_004,
        available_balance_minor=2_000_000_000,
        rolling_outflow_minor=3_000_000_000,
        pending_reserved_minor=500_000_000,
        sanctions_clear=True,
        sanctions_subject=intent.beneficiary_id,
        approvals=(
            BankApprovalV1(
                approval_id="approval-alice-9001",
                approver_id="human:alice",
                role="maker",
                intent_digest=intent.digest,
                approved_at="2026-08-19T09:55:00Z",
                expires_at="2026-08-19T10:10:00Z",
            ),
            BankApprovalV1(
                approval_id="approval-bob-9001",
                approver_id="human:bob",
                role="checker",
                intent_digest=intent.digest,
                approved_at="2026-08-19T09:56:00Z",
                expires_at="2026-08-19T10:10:00Z",
            ),
        ),
        observed_at="2026-08-19T09:59:30Z",
        valid_until="2026-08-19T10:02:00Z",
    )


def _authorization(
    intent: BankPaymentIntentV1,
    snapshot: BankControlSnapshotV1,
    policy: BankPolicyV1,
) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=intent.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:treasury-supervisor", "service:treasury"),
        audience="bank-executor:prod",
        purpose=intent.purpose,
        nonce="authorization-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest=snapshot.digest,
        policy_digest=policy.digest,
    )


def test_realistic_usd_12_5m_rtgs_controls_produce_allow_decision() -> None:
    intent = _intent()
    policy = _policy()
    snapshot = _snapshot(intent)
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)

    evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=_authorization(intent, snapshot, policy),
        policy=policy,
        signed_snapshot=sign_bank_snapshot(snapshot, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:00:01Z",
    )

    assert evaluation.decision.disposition is Disposition.ALLOW
    assert {result.reason_code for result in evaluation.decision.clause_results} == {
        "BANK_BENEFICIARY_ALLOWED",
        "BANK_SANCTIONS_CLEAR",
        "BANK_FUNDS_SUFFICIENT",
        "BANK_PAYMENT_LIMIT_OK",
        "BANK_ROLLING_LIMIT_OK",
        "BANK_APPROVAL_QUORUM_MET",
        "BANK_SEPARATION_OF_DUTIES_MET",
        "BANK_EVIDENCE_FRESH",
    }
    assert evaluation.snapshot.digest == snapshot.digest
    beneficiary_clause = next(
        result
        for result in evaluation.decision.clause_results
        if result.clause_id == "beneficiary-allowlist"
    )
    assert beneficiary_clause.details == {
        "beneficiary_digest": sha256_digest(intent.beneficiary_id.encode("utf-8"))
    }
    assert intent.beneficiary_id.encode("utf-8") not in evaluation.decision.to_bytes()


def test_stale_authoritative_snapshot_holds_instead_of_failing_open() -> None:
    intent = _intent()
    policy = _policy()
    snapshot = _snapshot(intent)
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)

    evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=_authorization(intent, snapshot, policy),
        policy=policy,
        signed_snapshot=sign_bank_snapshot(snapshot, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:03:00Z",
    )

    assert evaluation.decision.disposition is Disposition.HOLD
    assert any(
        result.reason_code == "BANK_EVIDENCE_STALE" for result in evaluation.decision.clause_results
    )


def test_amount_mutation_invalidates_approvals_and_cannot_reuse_authorization() -> None:
    original = _intent()
    changed = _intent(amount_minor=1_260_000_000)
    policy = _policy()
    snapshot = _snapshot(original)
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)
    changed_authorization = _authorization(changed, snapshot, policy)

    evaluation = BankingGate.evaluate(
        intent=changed,
        authorization=changed_authorization,
        policy=policy,
        signed_snapshot=sign_bank_snapshot(snapshot, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:00:01Z",
    )

    assert evaluation.decision.disposition is Disposition.DENY
    assert any(
        result.reason_code == "BANK_APPROVAL_INTENT_MISMATCH"
        for result in evaluation.decision.clause_results
    )


def test_policy_or_state_substitution_is_rejected_before_clause_evaluation() -> None:
    intent = _intent()
    policy = _policy()
    snapshot = _snapshot(intent)
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)
    authorization = _authorization(intent, snapshot, policy)

    with pytest.raises(BankingValidationError, match="policy"):
        BankingGate.evaluate(
            intent=intent,
            authorization=authorization,
            policy=replace(policy, per_payment_limit_minor=3_000_000_000),
            signed_snapshot=sign_bank_snapshot(snapshot, signer),
            evidence_keys=StaticKeyProvider.from_signers(signer),
            decision_at="2026-08-19T10:00:01Z",
        )

    other_snapshot = replace(snapshot, ledger_version=snapshot.ledger_version + 1)
    with pytest.raises(BankingValidationError, match="state"):
        BankingGate.evaluate(
            intent=intent,
            authorization=authorization,
            policy=policy,
            signed_snapshot=sign_bank_snapshot(other_snapshot, signer),
            evidence_keys=StaticKeyProvider.from_signers(signer),
            decision_at="2026-08-19T10:00:01Z",
        )


def test_sanctions_funds_and_aggregate_limits_are_hard_denials() -> None:
    intent = _intent()
    policy = _policy()
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)
    cases = (
        (replace(_snapshot(intent), sanctions_clear=False), policy, "BANK_SANCTIONS_BLOCKED"),
        (
            replace(_snapshot(intent), available_balance_minor=1_000_000_000),
            policy,
            "BANK_FUNDS_INSUFFICIENT",
        ),
        (
            replace(_snapshot(intent), rolling_outflow_minor=9_000_000_000),
            policy,
            "BANK_ROLLING_LIMIT_EXCEEDED",
        ),
        (
            _snapshot(intent),
            replace(policy, per_payment_limit_minor=1_000_000_000),
            "BANK_PAYMENT_LIMIT_EXCEEDED",
        ),
    )

    for snapshot, case_policy, expected_reason in cases:
        evaluation = BankingGate.evaluate(
            intent=intent,
            authorization=_authorization(intent, snapshot, case_policy),
            policy=case_policy,
            signed_snapshot=sign_bank_snapshot(snapshot, signer),
            evidence_keys=StaticKeyProvider.from_signers(signer),
            decision_at="2026-08-19T10:00:01Z",
        )
        assert evaluation.decision.disposition is Disposition.DENY
        assert expected_reason in {
            result.reason_code for result in evaluation.decision.clause_results
        }


def test_missing_approval_is_hold_but_duplicate_or_self_approval_is_denied() -> None:
    intent = _intent()
    policy = _policy()
    original = _snapshot(intent)
    signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)

    missing = replace(original, approvals=original.approvals[:1])
    missing_evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=_authorization(intent, missing, policy),
        policy=policy,
        signed_snapshot=sign_bank_snapshot(missing, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:00:01Z",
    )
    assert missing_evaluation.decision.disposition is Disposition.HOLD
    assert "BANK_APPROVAL_QUORUM_MISSING" in {
        result.reason_code for result in missing_evaluation.decision.clause_results
    }

    duplicate = replace(
        original,
        approvals=(
            original.approvals[0],
            replace(
                original.approvals[1],
                approval_id="approval-alice-second-role",
                approver_id="human:alice",
            ),
        ),
    )
    duplicate_evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=_authorization(intent, duplicate, policy),
        policy=policy,
        signed_snapshot=sign_bank_snapshot(duplicate, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:00:01Z",
    )
    assert duplicate_evaluation.decision.disposition is Disposition.DENY
    assert "BANK_APPROVER_DUPLICATED" in {
        result.reason_code for result in duplicate_evaluation.decision.clause_results
    }

    self_approved = replace(
        original,
        approvals=(
            replace(original.approvals[0], approver_id="agent:treasury-7"),
            original.approvals[1],
        ),
    )
    self_evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=_authorization(intent, self_approved, policy),
        policy=policy,
        signed_snapshot=sign_bank_snapshot(self_approved, signer),
        evidence_keys=StaticKeyProvider.from_signers(signer),
        decision_at="2026-08-19T10:00:01Z",
    )
    assert self_evaluation.decision.disposition is Disposition.DENY
    assert "BANK_SEPARATION_OF_DUTIES_VIOLATED" in {
        result.reason_code for result in self_evaluation.decision.clause_results
    }


def test_forged_snapshot_signature_never_reaches_policy_evaluation() -> None:
    intent = _intent()
    policy = _policy()
    snapshot = _snapshot(intent)
    trusted = Ed25519Signer.from_private_bytes("bank-evidence-key", _SEED)
    attacker = Ed25519Signer.generate("attacker-key")

    with pytest.raises(BankingValidationError, match="verification"):
        BankingGate.evaluate(
            intent=intent,
            authorization=_authorization(intent, snapshot, policy),
            policy=policy,
            signed_snapshot=sign_bank_snapshot(snapshot, attacker),
            evidence_keys=StaticKeyProvider.from_signers(trusted),
            decision_at="2026-08-19T10:00:01Z",
        )
