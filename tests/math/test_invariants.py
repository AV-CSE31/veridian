from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from veridian.math import (
    BoundInvariant,
    ConservationInvariant,
    EqualityInvariant,
    InvariantVerifier,
    LinearExpression,
    LinearTerm,
    MathInputError,
    MathStatus,
    ReasonCode,
)


def test_bank_posting_invariants_explain_exact_conservation_and_bounds() -> None:
    verifier = InvariantVerifier(
        (
            ConservationInvariant(
                invariant_id="posting-conservation",
                inflows=("source_debit_minor",),
                outflows=("beneficiary_credit_minor", "fee_minor"),
                unit="USD-cent",
            ),
            EqualityInvariant(
                invariant_id="ledger-observation-agrees",
                left=LinearExpression.field("observed_source_debit_minor", unit="USD-cent"),
                right=LinearExpression.field("source_debit_minor", unit="USD-cent"),
                tolerance=Decimal("0"),
            ),
            BoundInvariant(
                invariant_id="liquidity-floor",
                expression=LinearExpression.field("post_available_minor", unit="USD-cent"),
                lower=Decimal("5000000"),
            ),
        )
    )

    report = verifier.verify(
        {
            "source_debit_minor": 25_000_150,
            "beneficiary_credit_minor": 25_000_000,
            "fee_minor": 150,
            "observed_source_debit_minor": 25_000_150,
            "post_available_minor": 70_000_000,
        },
        evidence_ids=("bank-ledger-snapshot:8842",),
    )

    assert report.status is MathStatus.SATISFIED
    assert [result.reason_code for result in report.results] == [
        ReasonCode.CONSERVATION_SATISFIED,
        ReasonCode.EQUALITY_SATISFIED,
        ReasonCode.BOUNDS_SATISFIED,
    ]
    assert report.results[0].margin == Decimal("0")
    assert report.results[0].unit == "USD-cent"
    assert report.results[0].evidence_ids == ("bank-ledger-snapshot:8842",)
    assert report.results[0].counterexample is None
    assert "precision=50" in report.results[0].assumptions[-1]
    assert "ROUND_HALF_EVEN" in report.results[0].assumptions[-1]


def test_invariant_report_returns_counterexample_for_smallest_failed_clause() -> None:
    verifier = InvariantVerifier(
        (
            ConservationInvariant(
                invariant_id="posting-conservation",
                inflows=("source_debit_minor",),
                outflows=("beneficiary_credit_minor", "fee_minor"),
                unit="USD-cent",
            ),
            BoundInvariant(
                invariant_id="liquidity-floor",
                expression=LinearExpression.field("post_available_minor", unit="USD-cent"),
                lower=5_000_000,
            ),
        )
    )

    report = verifier.verify(
        {
            "source_debit_minor": 25_000_150,
            "beneficiary_credit_minor": 25_000_000,
            "fee_minor": 100,
            "post_available_minor": 4_999_999,
        }
    )

    assert report.status is MathStatus.VIOLATED
    conservation, bounds = report.results
    assert conservation.reason_code is ReasonCode.CONSERVATION_VIOLATED
    assert conservation.margin == Decimal("-50")
    assert conservation.counterexample is not None
    assert conservation.counterexample.clause_id == "posting-conservation"
    assert bounds.reason_code is ReasonCode.BOUNDS_VIOLATED
    assert bounds.margin == Decimal("-1")


def test_invariant_arithmetic_is_independent_of_caller_decimal_context() -> None:
    verifier = InvariantVerifier(
        (
            EqualityInvariant(
                invariant_id="one-cent-discrepancy",
                left=LinearExpression(
                    (
                        # 10_001 + 1 differs from the observed 10_003 by one cent.
                        # A 3-digit ambient context would incorrectly round both to 1.00E+4.
                        LinearTerm("posted_minor"),
                        LinearTerm("fee_minor"),
                    ),
                    unit="USD-cent",
                ),
                right=LinearExpression.field("observed_minor", unit="USD-cent"),
            ),
        )
    )

    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        report = verifier.verify({"posted_minor": 10_001, "fee_minor": 1, "observed_minor": 10_003})

    assert report.status is MathStatus.VIOLATED
    assert report.results[0].margin == Decimal("-1")


def test_missing_invariant_operand_is_unknown_not_a_false_pass() -> None:
    verifier = InvariantVerifier(
        (
            BoundInvariant(
                invariant_id="required-balance",
                expression=LinearExpression.field("balance_minor", unit="USD-cent"),
                lower=0,
            ),
        )
    )

    report = verifier.verify({})

    assert report.status is MathStatus.UNKNOWN
    assert report.results[0].reason_code is ReasonCode.INPUT_MISSING


def test_decimal_overflow_is_wrapped_in_veridian_error_hierarchy() -> None:
    verifier = InvariantVerifier(
        (
            BoundInvariant(
                invariant_id="bounded-number",
                expression=LinearExpression(
                    (LinearTerm("value", Decimal("1e999999999")),),
                    unit="unit",
                ),
                upper=Decimal("1e999999999"),
            ),
        )
    )

    with pytest.raises(MathInputError, match="decimal arithmetic failed"):
        verifier.verify({"value": Decimal("1e999999999")})
