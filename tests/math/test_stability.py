from decimal import Decimal

from veridian.math import (
    AffineBarrier,
    AffineTransition,
    BarrierVerifier,
    DisturbanceScenario,
    LinearExpression,
    LinearTerm,
    MathStatus,
    QuadraticPotential,
    QuadraticTerm,
    ReasonCode,
    RiskStabilityVerifier,
    StateEquation,
    VariableValue,
)


def test_barrier_verifier_reports_worst_case_liquidity_breach() -> None:
    transition = AffineTransition(
        (
            StateEquation(
                "available_next_minor",
                LinearExpression(
                    (
                        LinearTerm("available_minor", Decimal(1)),
                        LinearTerm("transfer_minor", Decimal(-1)),
                        LinearTerm("unexpected_outflow_minor", Decimal(-1)),
                    ),
                    unit="USD-cent",
                ),
            ),
        )
    )
    verifier = BarrierVerifier(
        clause_id="post-payment-liquidity-barrier",
        transition=transition,
        barriers=(
            AffineBarrier(
                barrier_id="minimum-operating-liquidity",
                expression=LinearExpression.field("available_next_minor", unit="USD-cent"),
                minimum=50_000_000,
            ),
        ),
        disturbances=(
            DisturbanceScenario(
                "normal-clearing",
                (VariableValue("unexpected_outflow_minor", 1_000_000),),
            ),
            DisturbanceScenario(
                "stress-outflow",
                (VariableValue("unexpected_outflow_minor", 30_000_000),),
                evidence_ids=("treasury-stress-scenario:v7",),
            ),
        ),
    )

    result = verifier.verify(
        state={"available_minor": 100_000_000},
        control={"transfer_minor": 25_000_000},
        evidence_ids=("core-banking-balance:991",),
    )

    assert result.status is MathStatus.VIOLATED
    assert result.reason_code is ReasonCode.BARRIER_BREACHED
    assert result.margin == Decimal("-5000000")
    assert result.counterexample is not None
    assert "stress-outflow" in result.counterexample.summary
    assert result.evidence_ids == (
        "core-banking-balance:991",
        "treasury-stress-scenario:v7",
    )
    assert "one-step model-relative safe-set check" in result.assumptions


def test_risk_stability_is_a_bounded_budget_check_not_a_universal_claim() -> None:
    transition = AffineTransition(
        (
            StateEquation(
                "risk_exposure",
                LinearExpression(
                    (
                        LinearTerm("risk_exposure", 1),
                        LinearTerm("approved_exposure_delta", 1),
                        LinearTerm("market_shock", 1),
                    ),
                    unit="risk-unit",
                ),
            ),
        )
    )
    verifier = RiskStabilityVerifier(
        clause_id="exposure-risk-budget",
        transition=transition,
        potential=QuadraticPotential(
            potential_id="squared-exposure",
            terms=(QuadraticTerm("risk_exposure", weight=1, center=0),),
            unit="risk-unit-squared",
        ),
        disturbances=(DisturbanceScenario("market-shock-1", (VariableValue("market_shock", 1),)),),
        contraction_factor=Decimal("1"),
        additive_budget=Decimal("50"),
    )

    result = verifier.verify(
        state={"risk_exposure": 10},
        control={"approved_exposure_delta": 2},
    )

    assert result.status is MathStatus.VIOLATED
    assert result.reason_code is ReasonCode.RISK_BUDGET_EXCEEDED
    # V(now)=100, V(next)=169, declared bound=150.
    assert result.margin == Decimal("-19")
    assert result.counterexample is not None
    assert "market-shock-1" in result.counterexample.summary
    assert "not a proof of agent or closed-loop stability" in result.assumptions


def test_safe_barrier_discloses_worst_disturbance_and_all_scenario_evidence() -> None:
    transition = AffineTransition(
        (
            StateEquation(
                "capacity_next",
                LinearExpression(
                    (
                        LinearTerm("capacity", 1),
                        LinearTerm("load", -1),
                        LinearTerm("shock", -1),
                    ),
                    unit="request",
                ),
            ),
        )
    )
    verifier = BarrierVerifier(
        clause_id="capacity-safe",
        transition=transition,
        barriers=(
            AffineBarrier(
                "reserve-capacity",
                LinearExpression.field("capacity_next", unit="request"),
                minimum=10,
            ),
        ),
        disturbances=(
            DisturbanceScenario(
                "low",
                (VariableValue("shock", 1),),
                evidence_ids=("scenario:low",),
            ),
            DisturbanceScenario(
                "high",
                (VariableValue("shock", 5),),
                evidence_ids=("scenario:high",),
            ),
        ),
    )

    result = verifier.verify(state={"capacity": 100}, control={"load": 60})

    assert result.status is MathStatus.SATISFIED
    assert result.reason_code is ReasonCode.BARRIER_SAFE
    assert result.margin == 25
    assert result.evidence_ids == ("scenario:low", "scenario:high")
    assert any(
        operand.name == "disturbance.shock" and operand.value == 5 for operand in result.operands
    )
