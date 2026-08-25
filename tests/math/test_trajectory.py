from veridian.math import (
    AtMostOnceRule,
    ForbiddenAfterRule,
    FreshnessRule,
    MathStatus,
    PrecedenceRule,
    ReasonCode,
    StateMachineMonitor,
    StateTransition,
    TerminalOutcomeRule,
    TrajectoryEvent,
    TrajectoryVerifier,
)


def _event(sequence: int, event_type: str, occurred_at_ms: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=f"evt-{sequence}",
        subject_id="payment-intent:sha256:4ad9",
        event_type=event_type,
        sequence=sequence,
        occurred_at_ms=occurred_at_ms,
        evidence_id=f"signed-event:{sequence}",
    )


def _bank_trajectory_verifier() -> TrajectoryVerifier:
    return TrajectoryVerifier(
        (
            StateMachineMonitor(
                rule_id="payment-state-machine",
                initial_state="proposed",
                transitions=(
                    StateTransition("proposed", "screened", "screened"),
                    StateTransition("screened", "authorized", "authorized"),
                    StateTransition("authorized", "permit_redeemed", "permitted"),
                    StateTransition("permitted", "dispatched", "in_flight"),
                    StateTransition("in_flight", "settled", "settled"),
                ),
                accepting_states=frozenset({"settled"}),
            ),
            PrecedenceRule("authorization-before-dispatch", "authorized", "dispatched"),
            AtMostOnceRule("single-use-permit", "permit_redeemed"),
            FreshnessRule(
                "screening-fresh-at-dispatch",
                evidence_event="screened",
                consuming_event="dispatched",
                max_age_ms=300_000,
            ),
            ForbiddenAfterRule("revoked-never-dispatches", "revoked", "dispatched"),
            TerminalOutcomeRule(
                "authorized-eventually-terminal",
                start_event="authorized",
                terminal_events=frozenset(
                    {"settled", "failed", "compensated", "expired", "reconciliation_hold"}
                ),
                max_delay_ms=600_000,
            ),
        )
    )


def test_authenticated_bank_payment_trajectory_satisfies_all_bounded_monitors() -> None:
    report = _bank_trajectory_verifier().verify(
        (
            _event(1, "screened", 1_000),
            _event(2, "authorized", 2_000),
            _event(3, "permit_redeemed", 3_000),
            _event(4, "dispatched", 4_000),
            _event(5, "settled", 5_000),
        ),
        complete=True,
    )

    assert report.status is MathStatus.SATISFIED
    assert len(report.results) == 6
    assert all(
        result.reason_code is ReasonCode.TRAJECTORY_RULE_SATISFIED for result in report.results
    )


def test_replayed_permit_and_stale_screening_have_concrete_witnesses() -> None:
    report = _bank_trajectory_verifier().verify(
        (
            _event(1, "screened", 1_000),
            _event(2, "authorized", 2_000),
            _event(3, "permit_redeemed", 3_000),
            _event(4, "permit_redeemed", 4_000),
            _event(5, "dispatched", 401_001),
            _event(6, "settled", 402_000),
        ),
        complete=True,
    )

    assert report.status is MathStatus.VIOLATED
    state_machine, _, single_use, freshness, _, _ = report.results
    assert state_machine.reason_code is ReasonCode.STATE_TRANSITION_INVALID
    assert state_machine.counterexample is not None
    assert state_machine.counterexample.event_index == 3
    assert single_use.reason_code is ReasonCode.TRAJECTORY_RULE_VIOLATED
    assert single_use.counterexample is not None
    assert "evt-4" in single_use.counterexample.summary
    assert freshness.reason_code is ReasonCode.TRAJECTORY_RULE_VIOLATED
    assert freshness.margin == -100_001


def test_open_trajectory_is_unknown_until_terminal_observation_window_closes() -> None:
    verifier = TrajectoryVerifier(
        (
            TerminalOutcomeRule(
                "dispatch-outcome",
                start_event="dispatched",
                terminal_events=frozenset({"settled", "failed"}),
                max_delay_ms=60_000,
            ),
        )
    )

    report = verifier.verify((_event(1, "dispatched", 10_000),), complete=False)

    assert report.status is MathStatus.UNKNOWN
    assert report.results[0].reason_code is ReasonCode.TRAJECTORY_INCOMPLETE
    assert "bounded observation" in report.results[0].assumptions


def test_unauthenticated_event_cannot_satisfy_a_temporal_rule() -> None:
    event = TrajectoryEvent(
        event_id="evt-untrusted",
        subject_id="payment:1",
        event_type="authorized",
        sequence=1,
        occurred_at_ms=1,
        evidence_id="agent-assertion:1",
        authenticated=False,
    )
    verifier = TrajectoryVerifier((AtMostOnceRule("authorization-once", "authorized"),))

    report = verifier.verify((event,), complete=True)

    assert report.status is MathStatus.UNKNOWN
    assert report.results[0].reason_code is ReasonCode.TRAJECTORY_UNAUTHENTICATED


def test_interleaved_subjects_use_sequence_order_without_cross_clock_assumption() -> None:
    events = (
        TrajectoryEvent(
            "a-1",
            "payment:a",
            "authorized",
            sequence=1,
            occurred_at_ms=1_000,
            evidence_id="signed:a-1",
        ),
        TrajectoryEvent(
            "b-1",
            "payment:b",
            "authorized",
            sequence=2,
            occurred_at_ms=500,
            evidence_id="signed:b-1",
        ),
    )
    verifier = TrajectoryVerifier((AtMostOnceRule("authorization-once", "authorized"),))

    report = verifier.verify(events, complete=True)

    assert report.status is MathStatus.SATISFIED
