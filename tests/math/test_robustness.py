from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from veridian.math import (
    ControlLevel,
    ControlPerturbation,
    DeltaComponent,
    MathInputError,
    MathStatus,
    MetamorphicRelation,
    MetamorphicVerifier,
    NumericPerturbation,
    PerturbationVerifier,
    ReasonCode,
    VectorNorm,
)


def test_perturbation_verifier_finds_smallest_bounded_counterexample() -> None:
    verifier = PerturbationVerifier(
        clause_id="fraud-score-local-robustness",
        norm=VectorNorm.L_INFINITY,
        radius=Decimal("0.10"),
        output_tolerance=Decimal("0.02"),
        input_unit="normalized-feature",
        output_unit="risk-score",
    )
    # Deliberately reverse severity: counterexample selection must not depend on input order.
    samples = (
        NumericPerturbation(
            case_id="larger-drift",
            delta=(DeltaComponent("beneficiary_novelty", Decimal("0.08")),),
            observed_output=Decimal("0.75"),
        ),
        NumericPerturbation(
            case_id="smallest-drift",
            delta=(DeltaComponent("beneficiary_novelty", Decimal("0.03")),),
            observed_output=Decimal("0.79"),
        ),
    )

    result = verifier.verify(Decimal("0.82"), samples)

    assert result.status is MathStatus.VIOLATED
    assert result.reason_code is ReasonCode.PERTURBATION_VIOLATED
    assert result.tolerance == Decimal("0.02")
    assert result.counterexample is not None
    assert result.counterexample.clause_id == "fraud-score-local-robustness"
    assert "smallest-drift" in result.counterexample.summary
    assert result.margin == Decimal("-0.01")
    assert "L-infinity" in result.derivation
    assert "finite supplied perturbation set" in result.assumptions


def test_metamarphic_control_cannot_weaken_when_risk_increases() -> None:
    verifier = MetamorphicVerifier(
        clause_id="amount-control-monotonicity",
        relation=MetamorphicRelation.CONTROL_NON_DECREASING,
        norm=VectorNorm.L1,
        radius=Decimal("25000000"),
        input_unit="USD-cent",
    )

    result = verifier.verify(
        ControlLevel.HOLD,
        (
            ControlPerturbation(
                case_id="amount-plus-250k",
                transformation_id="increase-transfer-amount",
                delta=(DeltaComponent("amount_minor", 25_000_000),),
                observed=ControlLevel.ALLOW,
            ),
        ),
    )

    assert result.status is MathStatus.VIOLATED
    assert result.reason_code is ReasonCode.METAMORPHIC_VIOLATED
    assert result.counterexample is not None
    assert "HOLD -> ALLOW" in result.counterexample.summary
    assert "monotone non-decreasing control" in result.derivation


def test_binary_float_is_rejected_instead_of_silently_rounded() -> None:
    with pytest.raises(MathInputError, match="integer or Decimal"):
        NumericPerturbation(
            case_id="inexact",
            delta=(DeltaComponent("amount", 0.1),),  # type: ignore[arg-type]
            observed_output=Decimal("1"),
        )


def test_l2_result_is_reproducible_and_discloses_worst_passing_sample() -> None:
    verifier = PerturbationVerifier(
        clause_id="bounded-vector-robustness",
        norm=VectorNorm.L2,
        radius=Decimal("5"),
        output_tolerance=Decimal("0.5"),
        input_unit="feature-unit",
        output_unit="score",
    )
    sample = NumericPerturbation(
        case_id="three-four-five",
        delta=(DeltaComponent("x", 3), DeltaComponent("y", 4)),
        observed_output=Decimal("10.4"),
        evidence_ids=("evaluation:3-4-5",),
    )

    # Caller Decimal context must not change the verifier's L2 calculation.
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        result = verifier.verify(Decimal("10"), (sample,))

    assert result.status is MathStatus.SATISFIED
    assert result.reason_code is ReasonCode.PERTURBATION_ROBUST
    assert result.margin == Decimal("0.1")
    assert any(
        operand.name == "perturbation_norm" and operand.value == 5 for operand in result.operands
    )


def test_invariant_metamorphic_result_discloses_transformation_delta() -> None:
    verifier = MetamorphicVerifier(
        clause_id="memo-is-control-irrelevant",
        relation=MetamorphicRelation.INVARIANT,
        norm=VectorNorm.L_INFINITY,
        radius=1,
        input_unit="categorical-change",
    )

    result = verifier.verify(
        ControlLevel.ALLOW,
        (
            ControlPerturbation(
                case_id="memo-normalization",
                transformation_id="normalize-payment-memo",
                delta=(DeltaComponent("memo_changed", 1),),
                observed=ControlLevel.ALLOW,
            ),
        ),
    )

    assert result.status is MathStatus.SATISFIED
    assert any(operand.name == "memo_changed" for operand in result.operands)
