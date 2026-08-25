from veridian.math import (
    AggregateEvent,
    AggregateVerifier,
    MathStatus,
    ReasonCode,
)


def _transfer(
    event_id: str,
    group_id: str,
    occurred_at_ms: int,
    amount_minor: int,
    *,
    authenticated: bool = True,
) -> AggregateEvent:
    return AggregateEvent(
        event_id=event_id,
        group_id=group_id,
        occurred_at_ms=occurred_at_ms,
        amount=amount_minor,
        evidence_id=f"bank-posting:{event_id}",
        authenticated=authenticated,
    )


def test_aggregate_verifier_detects_split_payment_limit_evasion() -> None:
    verifier = AggregateVerifier(
        clause_id="principal-beneficiary-24h-limit",
        window_ms=86_400_000,
        limit=100_000_000,
        unit="USD-cent",
    )
    group = "principal:42|beneficiary:77"

    result = verifier.verify(
        (
            _transfer("wire-1", group, 10_000, 40_000_000),
            _transfer("wire-2", group, 20_000, 35_000_000),
            _transfer("wire-3", group, 30_000, 30_000_000),
            _transfer("other", "principal:42|beneficiary:88", 35_000, 90_000_000),
        ),
        as_of_ms=40_000,
    )

    assert result.status is MathStatus.VIOLATED
    assert result.reason_code is ReasonCode.AGGREGATE_LIMIT_EXCEEDED
    assert result.margin == -5_000_000
    assert result.counterexample is not None
    assert group in result.counterexample.summary
    assert len([item for item in result.operands if item.name.startswith("event[")]) == 3


def test_unauthenticated_in_window_amount_makes_aggregate_unknown() -> None:
    verifier = AggregateVerifier(
        clause_id="beneficiary-window",
        window_ms=1_000,
        limit=100,
        unit="USD-cent",
    )

    result = verifier.verify(
        (_transfer("agent-claim", "beneficiary:77", 500, 10, authenticated=False),),
        as_of_ms=1_000,
    )

    assert result.status is MathStatus.UNKNOWN
    assert result.reason_code is ReasonCode.AGGREGATE_UNAUTHENTICATED


def test_aggregate_result_is_independent_of_input_iteration_order() -> None:
    verifier = AggregateVerifier(
        clause_id="ordered-window",
        window_ms=10_000,
        limit=100,
        unit="USD-cent",
    )
    events = (
        _transfer("later", "beneficiary:77", 2_000, 20),
        _transfer("earlier", "beneficiary:77", 1_000, 10),
    )

    forward = verifier.verify(events, as_of_ms=3_000)
    reverse = verifier.verify(tuple(reversed(events)), as_of_ms=3_000)

    assert forward == reverse
