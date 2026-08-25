from decimal import Decimal

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


def _event(sequence: int, event_type: str, time_ms: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=f"wire-8842:{sequence}",
        subject_id="payment-intent:sha256:4ad9",
        event_type=event_type,
        sequence=sequence,
        occurred_at_ms=time_ms,
        evidence_id=f"signed-bank-event:{sequence}",
    )


def test_industrial_bank_payment_pack_verifies_action_and_completion_math() -> None:
    policy = BankPaymentMathPolicy(
        currency="USD",
        minor_unit_scale=2,
        per_payment_limit_minor=100_000_000,
        liquidity_floor_minor=50_000_000,
        amount_perturbation_radius_minor=50_000_000,
        screening_max_age_ms=300_000,
        terminal_outcome_max_delay_ms=600_000,
        liquidity_stresses=(
            BankLiquidityStress("normal-clearing", 1_000_000),
            BankLiquidityStress(
                "treasury-stress-v7",
                10_000_000,
                evidence_ids=("treasury-model:v7",),
            ),
        ),
    )
    suite = build_bank_payment_math(policy)

    assessment = suite.verify(
        accounting_snapshot={
            "source_debit_minor": 25_000_150,
            "beneficiary_credit_minor": 25_000_000,
            "fee_minor": 150,
            "transfer_minor": 25_000_000,
            "post_available_minor": 75_000_000,
        },
        liquidity_state={"available_minor": 110_000_000},
        liquidity_control={"transfer_minor": 25_000_000},
        baseline_control=ControlLevel.HOLD,
        amount_perturbations=(
            ControlPerturbation(
                case_id="amount-plus-250k",
                transformation_id="increase-transfer-amount",
                delta=(DeltaComponent("amount_minor", 25_000_000),),
                observed=ControlLevel.DENY,
            ),
        ),
        trajectory=(
            _event(1, "screened", 1_000),
            _event(2, "authorized", 2_000),
            _event(3, "permit_redeemed", 3_000),
            _event(4, "dispatched", 4_000),
            _event(5, "settled", 5_000),
        ),
        trajectory_complete=True,
        evidence_ids=("core-banking-snapshot:8842",),
    )

    assert assessment.status is MathStatus.SATISFIED
    assert len(assessment.results) == 13
    assert assessment.liquidity.margin == Decimal("25000000")
    assert assessment.accounting.results[0].unit == "USD-cent"
    assert "synthetic policy" in assessment.assumptions
