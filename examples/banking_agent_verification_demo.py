"""Offline industrial RTGS assurance showcase.

An untrusted agent proposes a USD 12.5 million payment. Veridian binds the
exact action to authenticated controls, checks two-person approval and
separation of duties, evaluates deterministic payment mathematics, issues a
signed single-use permit, and executes through an idempotent synthetic rail.

The synthetic rail and the fixed demonstration keys are deliberately local.
They model the trust boundaries without using credentials or network access;
they are not a connector to a live payment scheme.

Run:
    python examples/banking_agent_verification_demo.py
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
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
    BankingValidationError,
    BankPaymentIntentV1,
    BankPolicyV1,
    SyntheticRtgsAdapter,
    sign_bank_snapshot,
    verify_bank_settlement,
)
from veridian.effects import (
    ExecutionPermitV1,
    PermitError,
    SqlitePermitStore,
    TrustedExecutor,
    sign_execution_permit,
    verify_effect_receipt,
)
from veridian.math import (
    BankLiquidityStress,
    BankPaymentAssessment,
    BankPaymentMathPolicy,
    ControlLevel,
    ControlPerturbation,
    DeltaComponent,
    MathStatus,
    TrajectoryEvent,
    build_bank_payment_math,
)

# Public, fixed test vectors make the example reproducible. Never use these
# private-key seeds outside an offline demonstration.
_EVIDENCE_SEED = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
_EXECUTOR_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
_LEDGER_SEED = bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7")


def _agent_adapter() -> OpenAIResponsesAdapter:
    return OpenAIResponsesAdapter(
        {
            "submit_bank_payment": ActionSpecV1(
                "bank.payment.submit",
                "creditor_account_id",
            )
        }
    )


def _normalize_agent_payment(arguments: dict[str, object]) -> BankPaymentIntentV1:
    proposal = _agent_adapter().normalize(
        {
            "type": "function_call",
            "call_id": "call-agent-payment-9001",
            "name": "submit_bank_payment",
            "arguments": encode_profile_v1(arguments).decode(),
        }
    )
    return BankPaymentIntentV1.from_bytes(proposal.semantics.to_bytes())


def _evaluate_math(
    *,
    intent: BankPaymentIntentV1,
    policy: BankPolicyV1,
    snapshot: BankControlSnapshotV1,
) -> tuple[BankPaymentAssessment, bool]:
    suite = build_bank_payment_math(
        BankPaymentMathPolicy(
            currency="USD",
            minor_unit_scale=2,
            per_payment_limit_minor=policy.per_payment_limit_minor,
            liquidity_floor_minor=policy.liquidity_buffer_minor,
            amount_perturbation_radius_minor=100_000_000,
            screening_max_age_ms=300_000,
            terminal_outcome_max_delay_ms=600_000,
            liquidity_stresses=(
                BankLiquidityStress(
                    "rtgs-stress-unexpected-500k-outflow",
                    50_000_000,
                    evidence_ids=(snapshot.evidence_id,),
                ),
            ),
        )
    )
    accounting_snapshot = {
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
    }
    liquidity_state = {
        "available_minor": snapshot.available_balance_minor - snapshot.pending_reserved_minor
    }
    liquidity_control = {"transfer_minor": intent.amount_minor + intent.fee_minor}
    amount_perturbations = (
        ControlPerturbation(
            case_id="amount-plus-one-million",
            transformation_id="increase-transfer-amount",
            delta=(DeltaComponent("amount_minor", Decimal(100_000_000)),),
            observed=ControlLevel.DENY,
        ),
    )
    trajectory = tuple(
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
    )
    first = suite.verify(
        accounting_snapshot=accounting_snapshot,
        liquidity_state=liquidity_state,
        liquidity_control=liquidity_control,
        baseline_control=ControlLevel.ALLOW,
        amount_perturbations=amount_perturbations,
        trajectory=trajectory,
        trajectory_complete=True,
        evidence_ids=(snapshot.evidence_id,),
    )
    second = suite.verify(
        accounting_snapshot=accounting_snapshot,
        liquidity_state=liquidity_state,
        liquidity_control=liquidity_control,
        baseline_control=ControlLevel.ALLOW,
        amount_perturbations=amount_perturbations,
        trajectory=trajectory,
        trajectory_complete=True,
        evidence_ids=(snapshot.evidence_id,),
    )
    return first, first == second


def run_showcase(database: Path) -> dict[str, object]:
    """Run the full scenario and return a machine-readable assurance summary."""

    agent_arguments: dict[str, object] = {
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
    intent = _normalize_agent_payment(agent_arguments)
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

    evidence_signer = Ed25519Signer.from_private_bytes(
        "bank-evidence-key",
        _EVIDENCE_SEED,
    )
    signed_snapshot = sign_bank_snapshot(snapshot, evidence_signer)
    evaluation = BankingGate.evaluate(
        intent=intent,
        authorization=authorization,
        policy=policy,
        signed_snapshot=signed_snapshot,
        evidence_keys=StaticKeyProvider.from_signers(evidence_signer),
        decision_at="2026-08-19T10:00:01Z",
    )
    if evaluation.decision.disposition is not Disposition.ALLOW:
        raise RuntimeError("the approved demonstration payment was not allowed")

    math_assessment, math_repeatable = _evaluate_math(
        intent=intent,
        policy=policy,
        snapshot=snapshot,
    )
    if math_assessment.status is not MathStatus.SATISFIED:
        raise RuntimeError("the demonstration payment failed its mathematical checks")

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
    executor_keys = StaticKeyProvider.from_signers(executor_signer)
    rail = SyntheticRtgsAdapter(
        settlement_signer=ledger_signer,
        observed_at="2026-08-19T10:00:14Z",
        starting_ledger_version=snapshot.ledger_version,
    )
    executor = TrustedExecutor(
        audience="bank-executor:prod",
        store=SqlitePermitStore(database),
        permit_keys=executor_keys,
        receipt_keys=executor_keys,
        receipt_signer=executor_signer,
        adapter=rail,
    )
    signed_permit = sign_execution_permit(permit, executor_signer)
    outcome = executor.execute(
        signed_permit=signed_permit,
        semantics=intent.to_action_semantics(),
        current_state_digest=snapshot.digest,
        current_policy_digest=policy.digest,
        executed_at="2026-08-19T10:00:10Z",
    )
    retry = executor.execute(
        signed_permit=signed_permit,
        semantics=intent.to_action_semantics(),
        current_state_digest=snapshot.digest,
        current_policy_digest=policy.digest,
        executed_at="2026-08-19T10:00:11Z",
    )

    verified_effect = verify_effect_receipt(outcome.receipt_envelope, keys=executor_keys)
    settlement = verify_bank_settlement(
        rail.settlement_envelope(permit.idempotency_key),
        keys=StaticKeyProvider.from_signers(ledger_signer),
        intent=intent,
        expected_permit_digest=permit.digest,
    )

    tampered_arguments = dict(agent_arguments)
    tampered_arguments["amount_minor"] = intent.amount_minor + 100  # USD 1.00
    tampered_intent = _normalize_agent_payment(tampered_arguments)
    gate_tamper_error = ""
    try:
        BankingGate.evaluate(
            intent=tampered_intent,
            authorization=authorization,
            policy=policy,
            signed_snapshot=signed_snapshot,
            evidence_keys=StaticKeyProvider.from_signers(evidence_signer),
            decision_at="2026-08-19T10:00:12Z",
        )
    except BankingValidationError as exc:
        gate_tamper_error = str(exc)

    executor_tamper_error = ""
    try:
        executor.execute(
            signed_permit=signed_permit,
            semantics=tampered_intent.to_action_semantics(),
            current_state_digest=snapshot.digest,
            current_policy_digest=policy.digest,
            executed_at="2026-08-19T10:00:12Z",
        )
    except PermitError as exc:
        executor_tamper_error = str(exc)

    clause_statuses = {
        clause.clause_id: clause.status.value for clause in evaluation.decision.clause_results
    }
    result: dict[str, object] = {
        "scenario": "offline-industrial-rtgs",
        "action": {
            "payment_id": intent.payment_id,
            "amount": "USD 12,500,000.00",
            "amount_minor": intent.amount_minor,
            "rail": intent.rail,
            "transport": "openai.responses",
        },
        "authorization": {
            "decision": evaluation.decision.disposition.value,
            "exact_semantic_binding": (
                authorization.semantic_digest
                == intent.digest
                == permit.semantic_digest
                == verified_effect.receipt.semantic_digest
            ),
            "state_bound": authorization.state_digest == snapshot.digest,
            "policy_bound": authorization.policy_digest == policy.digest,
        },
        "controls": {
            "approval_quorum": clause_statuses["approval-quorum"],
            "approver_count": len(snapshot.approvals),
            "approver_roles": sorted(approval.role for approval in snapshot.approvals),
            "separation_of_duties": clause_statuses["separation-of-duties"],
            "agent_is_approver": authorization.principal_id
            in {approval.approver_id for approval in snapshot.approvals},
        },
        "mathematics": {
            "status": math_assessment.status.value,
            "repeatable": math_repeatable,
            "clauses": {item.clause_id: item.status.value for item in math_assessment.results},
        },
        "execution": {
            "permit_max_uses": permit.max_uses,
            "first_call_replayed": outcome.replayed,
            "exact_retry_replayed": retry.replayed,
            "same_receipt_on_retry": retry.receipt.digest == outcome.receipt.digest,
            "economic_effect_count": rail.economic_effect_count,
        },
        "postconditions": {
            "effect_receipt_verified": (verified_effect.receipt.permit_digest == permit.digest),
            "settlement_status": settlement.receipt.status.value,
            "settlement_satisfied": settlement.satisfied,
            "completion_asserted": settlement.completion is not None,
            "ledger_version_advanced": (
                settlement.receipt.ledger_version_after > settlement.receipt.ledger_version_before
            ),
        },
        "tamper": {
            "change": "amount_minor + 100 (USD 1.00)",
            "semantic_digest_changed": tampered_intent.digest != intent.digest,
            "gate_rejected": bool(gate_tamper_error),
            "executor_rejected": bool(executor_tamper_error),
            "gate_reason": gate_tamper_error,
            "executor_reason": executor_tamper_error,
            "economic_effect_count_after_attempt": rail.economic_effect_count,
        },
    }
    if not (
        math_repeatable
        and retry.replayed
        and rail.economic_effect_count == 1
        and settlement.satisfied
        and gate_tamper_error
        and executor_tamper_error
    ):
        raise RuntimeError("the end-to-end assurance demonstration did not close safely")
    return result


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="veridian-banking-demo-") as temporary:
        result = run_showcase(Path(temporary) / "effects.db")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
