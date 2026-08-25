from __future__ import annotations

from pathlib import Path

from veridian.adapters import ActionSpecV1, OpenAIResponsesAdapter
from veridian.assurance import (
    AuthorizationEnvelope,
    Disposition,
    Ed25519Signer,
    StaticKeyProvider,
    encode_profile_v1,
)
from veridian.banking import (
    BankApprovalV1,
    BankControlSnapshotV1,
    BankingGate,
    BankPaymentIntentV1,
    BankPolicyV1,
    SyntheticRtgsAdapter,
    sign_bank_snapshot,
    verify_bank_settlement,
)
from veridian.effects import (
    ExecutionPermitV1,
    SqlitePermitStore,
    TrustedExecutor,
    sign_execution_permit,
    verify_effect_receipt,
)
from veridian.math import (
    BankLiquidityStress,
    BankPaymentMathPolicy,
    ControlLevel,
    ControlPerturbation,
    DeltaComponent,
    MathStatus,
    TrajectoryEvent,
    build_bank_payment_math,
)

_EVIDENCE_SEED = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
_EXECUTOR_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
_LEDGER_SEED = bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")


def test_usd_12_5m_agent_payment_is_verified_executed_once_and_reconciled(
    tmp_path: Path,
) -> None:
    agent_arguments = {
        "payment_id": "PAY-2026-0009001",
        "debtor_account_id": "account:treasury-usd-001",
        "creditor_account_id": "account:acme-usd-042",
        "beneficiary_id": "beneficiary:acme-industrial",
        "amount_minor": 1_250_000_000,
        "fee_minor": 2_500,
        "currency": "USD",
        "value_date": "2026-08-19",
        "rail": "RTGS",
        "purpose": "invoice:INV-314",
    }
    proposed = OpenAIResponsesAdapter(
        {"submit_bank_payment": ActionSpecV1("bank.payment.submit", "creditor_account_id")}
    ).normalize(
        {
            "type": "function_call",
            "call_id": "call-agent-payment-9001",
            "name": "submit_bank_payment",
            "arguments": encode_profile_v1(agent_arguments).decode(),
        }
    )
    intent = BankPaymentIntentV1.from_bytes(proposed.semantics.to_bytes())
    assert proposed.transport.protocol == "openai.responses"
    policy = BankPolicyV1(
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
    snapshot = BankControlSnapshotV1(
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
                "approval-alice-9001",
                "human:alice",
                "maker",
                intent.digest,
                "2026-08-19T09:55:00Z",
                "2026-08-19T10:10:00Z",
            ),
            BankApprovalV1(
                "approval-bob-9001",
                "human:bob",
                "checker",
                intent.digest,
                "2026-08-19T09:56:00Z",
                "2026-08-19T10:10:00Z",
            ),
        ),
        observed_at="2026-08-19T09:59:30Z",
        valid_until="2026-08-19T10:02:00Z",
    )
    authorization = AuthorizationEnvelope(
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
    evidence_signer = Ed25519Signer.from_private_bytes("bank-evidence-key", _EVIDENCE_SEED)
    evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=authorization,
        policy=policy,
        signed_snapshot=sign_bank_snapshot(snapshot, evidence_signer),
        evidence_keys=StaticKeyProvider.from_signers(evidence_signer),
        decision_at="2026-08-19T10:00:01Z",
    )
    assert evaluation.decision.disposition is Disposition.ALLOW

    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=evaluation.decision,
        permit_id="permit-rtgs-0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-2026-0009001",
        issued_at="2026-08-19T10:00:02Z",
        not_before="2026-08-19T10:00:02Z",
        expires_at="2026-08-19T10:02:00Z",
    )
    executor_signer = Ed25519Signer.from_private_bytes("executor-key", _EXECUTOR_SEED)
    ledger_signer = Ed25519Signer.from_private_bytes("bank-ledger-key", _LEDGER_SEED)
    rail = SyntheticRtgsAdapter(
        settlement_signer=ledger_signer,
        observed_at="2026-08-19T10:00:14Z",
        starting_ledger_version=snapshot.ledger_version,
    )
    executor_keys = StaticKeyProvider.from_signers(executor_signer)
    executor = TrustedExecutor(
        audience="bank-executor:prod",
        store=SqlitePermitStore(tmp_path / "effects.db"),
        permit_keys=executor_keys,
        receipt_keys=executor_keys,
        receipt_signer=executor_signer,
        adapter=rail,
    )

    outcome = executor.execute(
        signed_permit=sign_execution_permit(permit, executor_signer),
        semantics=intent.to_action_semantics(),
        current_state_digest=snapshot.digest,
        current_policy_digest=policy.digest,
        executed_at="2026-08-19T10:00:10Z",
    )
    replay = executor.execute(
        signed_permit=sign_execution_permit(permit, executor_signer),
        semantics=intent.to_action_semantics(),
        current_state_digest=snapshot.digest,
        current_policy_digest=policy.digest,
        executed_at="2026-08-19T10:00:11Z",
    )

    effect_receipt = verify_effect_receipt(outcome.receipt_envelope, keys=executor_keys)
    settlement = verify_bank_settlement(
        rail.settlement_envelope(permit.idempotency_key),
        keys=StaticKeyProvider.from_signers(ledger_signer),
        intent=intent,
        expected_permit_digest=permit.digest,
    )

    assert rail.economic_effect_count == 1
    assert replay.replayed is True
    assert effect_receipt.receipt.semantic_digest == intent.digest
    assert settlement.satisfied
    assert settlement.completion is not None
    assert settlement.completion.assertions["permit_digest"] == permit.digest

    math_suite = build_bank_payment_math(
        BankPaymentMathPolicy(
            currency="USD",
            minor_unit_scale=2,
            per_payment_limit_minor=policy.per_payment_limit_minor,
            liquidity_floor_minor=policy.liquidity_buffer_minor,
            amount_perturbation_radius_minor=100_000_000,
            screening_max_age_ms=300_000,
            terminal_outcome_max_delay_ms=600_000,
            liquidity_stresses=(BankLiquidityStress("rtgs-stress", 50_000_000),),
        )
    )
    assessment = math_suite.verify(
        accounting_snapshot={
            "source_debit_minor": intent.amount_minor + intent.fee_minor,
            "beneficiary_credit_minor": intent.amount_minor,
            "fee_minor": intent.fee_minor,
            "transfer_minor": intent.amount_minor,
            "post_available_minor": (
                snapshot.available_balance_minor
                - snapshot.pending_reserved_minor
                - intent.amount_minor
                - intent.fee_minor
            ),
        },
        liquidity_state={
            "available_minor": snapshot.available_balance_minor - snapshot.pending_reserved_minor
        },
        liquidity_control={"transfer_minor": intent.amount_minor + intent.fee_minor},
        baseline_control=ControlLevel.ALLOW,
        amount_perturbations=(
            ControlPerturbation(
                case_id="amount-plus-one-million",
                transformation_id="increase-transfer-amount",
                delta=(DeltaComponent("amount_minor", 100_000_000),),
                observed=ControlLevel.DENY,
            ),
        ),
        trajectory=tuple(
            TrajectoryEvent(
                event_id=f"{intent.payment_id}:{sequence}",
                subject_id=f"payment-intent:{intent.digest}",
                event_type=event_type,
                sequence=sequence,
                occurred_at_ms=occurred_at_ms,
                evidence_id=f"signed-bank-event:{sequence}",
            )
            for sequence, event_type, occurred_at_ms in (
                (1, "screened", 1_000),
                (2, "authorized", 2_000),
                (3, "permit_redeemed", 3_000),
                (4, "dispatched", 4_000),
                (5, "settled", 5_000),
            )
        ),
        trajectory_complete=True,
        evidence_ids=(snapshot.evidence_id,),
    )
    assert assessment.status is MathStatus.SATISFIED
