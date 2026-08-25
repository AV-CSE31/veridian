from decimal import Decimal

from veridian.math import (
    ControlLevel,
    ControlPerturbation,
    DeltaComponent,
    DeploymentControlSnapshot,
    DeploymentEvidence,
    DeploymentMathPolicy,
    DeploymentRiskDisturbance,
    MathStatus,
    TrajectoryEvent,
    build_deployment_math,
)


def _policy() -> DeploymentMathPolicy:
    return DeploymentMathPolicy(
        required_approval_quorum=2,
        required_evidence_kinds=frozenset(
            {
                "artifact-attestation",
                "canary-observation",
                "change-ticket",
                "rollback-attestation",
                "slo-snapshot",
            }
        ),
        evidence_max_age_ms=10_000,
        change_window_start_ms=20_000,
        change_window_end_ms=80_000,
        maximum_canary_error_rate_bps=25,
        minimum_error_budget_reserve_bps=40,
        risk_perturbation_radius_bps=100,
        terminal_outcome_max_delay_ms=30_000,
        disturbances=(
            DeploymentRiskDisturbance("nominal", 0),
            DeploymentRiskDisturbance(
                "regional-failover-v4",
                15,
                evidence_ids=("risk-model:regional-failover-v4",),
            ),
        ),
    )


def _evidence(*, include_rollback: bool = True) -> tuple[DeploymentEvidence, ...]:
    records = [
        DeploymentEvidence("sig:change-441", "change-ticket", "change-system", 19_000, True),
        DeploymentEvidence(
            "sig:artifact-a91", "artifact-attestation", "build-service", 19_100, True
        ),
        DeploymentEvidence(
            "sig:canary-441", "canary-observation", "telemetry-service", 22_000, True
        ),
        DeploymentEvidence("sig:slo-441", "slo-snapshot", "sre-telemetry", 22_100, True),
        DeploymentEvidence("sig:approval-risk", "approval", "risk-officer-17", 22_200, True),
        DeploymentEvidence("sig:approval-sre", "approval", "sre-approver-8", 22_300, True),
    ]
    if include_rollback:
        records.append(
            DeploymentEvidence(
                "sig:rollback-a91",
                "rollback-attestation",
                "release-engineering",
                19_200,
                True,
            )
        )
    return tuple(records)


def _snapshot(**overrides: object) -> DeploymentControlSnapshot:
    values: dict[str, object] = {
        "remaining_error_budget_bps": 100,
        "projected_deployment_burn_bps": 20,
        "canary_error_rate_bps": 8,
        "deployment_at_ms": 25_000,
        "rollback_ready": True,
    }
    values.update(overrides)
    return DeploymentControlSnapshot(**values)  # type: ignore[arg-type]


def _event(
    sequence: int,
    event_type: str,
    time_ms: int,
    *,
    authenticated: bool = True,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=f"deploy:payments-api:sha256-a91:{sequence}",
        subject_id="deployment:payments-api:sha256-a91",
        event_type=event_type,
        sequence=sequence,
        occurred_at_ms=time_ms,
        evidence_id=f"signed-deployment-event:{sequence}",
        authenticated=authenticated,
    )


def _successful_trajectory() -> tuple[TrajectoryEvent, ...]:
    return (
        _event(1, "change_approved", 20_000),
        _event(2, "canary_started", 21_000),
        _event(3, "canary_passed", 22_000),
        _event(4, "deployed", 25_000),
        _event(5, "succeeded", 28_000),
    )


def _verify(
    *,
    snapshot: DeploymentControlSnapshot | None = None,
    evidence: tuple[DeploymentEvidence, ...] | None = None,
    perturbations: tuple[ControlPerturbation, ...] | None = None,
    trajectory: tuple[TrajectoryEvent, ...] | None = None,
):
    suite = build_deployment_math(_policy())
    return suite.verify(
        snapshot=snapshot or _snapshot(),
        evidence=_evidence() if evidence is None else evidence,
        requester_principal_id="product-owner-3",
        deployer_principal_id="release-operator-6",
        baseline_control=ControlLevel.HOLD,
        risk_perturbations=perturbations
        or (
            ControlPerturbation(
                case_id="double-canary-error",
                transformation_id="increase-observed-canary-risk",
                delta=(DeltaComponent("canary_error_rate_bps", 8),),
                observed=ControlLevel.DENY,
                evidence_ids=("eval:double-canary-error",),
            ),
        ),
        trajectory=trajectory or _successful_trajectory(),
        trajectory_complete=True,
    )


def test_high_risk_bank_service_deployment_satisfies_all_declared_controls() -> None:
    assessment = _verify()

    assert assessment.status is MathStatus.SATISFIED
    assert assessment.recommended_control is ControlLevel.ALLOW
    assert assessment.error_budget.margin == Decimal("25")
    assert assessment.evidence.status is MathStatus.SATISFIED
    assert assessment.preflight.status is MathStatus.SATISFIED
    assert assessment.trajectory.status is MathStatus.SATISFIED
    assert all(result.assumptions for result in assessment.results)
    assert all(result.margin is not None for result in assessment.results)
    assert all(result.counterexample is None for result in assessment.results)
    assert "model-relative deployment verification" in assessment.assumptions


def test_riskier_perturbation_cannot_weaken_the_control_decision() -> None:
    assessment = _verify(
        perturbations=(
            ControlPerturbation(
                case_id="higher-canary-error",
                transformation_id="increase-observed-canary-risk",
                delta=(DeltaComponent("canary_error_rate_bps", 12),),
                observed=ControlLevel.ALLOW,
                evidence_ids=("eval:higher-canary-error",),
            ),
        )
    )

    assert assessment.risk_control.status is MathStatus.VIOLATED
    assert assessment.risk_control.margin == Decimal("-1")
    assert assessment.risk_control.counterexample is not None
    assert assessment.recommended_control is ControlLevel.DENY


def test_error_budget_barrier_rejects_slo_exhaustion_under_declared_disturbance() -> None:
    assessment = _verify(
        snapshot=_snapshot(
            remaining_error_budget_bps=70,
            projected_deployment_burn_bps=20,
        )
    )

    assert assessment.error_budget.status is MathStatus.VIOLATED
    assert assessment.error_budget.margin == Decimal("-5")
    assert assessment.error_budget.counterexample is not None
    assert "regional-failover-v4" in assessment.error_budget.counterexample.summary


def test_duplicate_deployment_and_deploy_after_revocation_are_rejected() -> None:
    duplicate = _verify(
        trajectory=(
            *_successful_trajectory()[:-1],
            _event(5, "deployed", 26_000),
            _event(6, "succeeded", 28_000),
        )
    )
    after_revocation = _verify(
        trajectory=(
            _event(1, "change_approved", 20_000),
            _event(2, "revoked", 21_000),
            _event(3, "deployed", 25_000),
        )
    )

    assert any(
        result.clause_id == "deployment.at_most_once"
        and result.status is MathStatus.VIOLATED
        and result.counterexample is not None
        for result in duplicate.trajectory.results
    )
    assert any(
        result.clause_id == "deployment.revocation_is_final"
        and result.status is MathStatus.VIOLATED
        and result.counterexample is not None
        for result in after_revocation.trajectory.results
    )


def test_stale_or_unauthenticated_trajectory_cannot_authorize_deployment() -> None:
    stale = _verify(
        trajectory=(
            _event(1, "change_approved", 20_000),
            _event(2, "canary_started", 65_000),
            _event(3, "canary_passed", 66_000),
            _event(4, "deployed", 70_001),
            _event(5, "succeeded", 71_000),
        )
    )
    unauthenticated = _verify(
        trajectory=(
            _event(1, "change_approved", 20_000),
            _event(2, "canary_started", 21_000),
            _event(3, "canary_passed", 22_000, authenticated=False),
            _event(4, "deployed", 25_000),
            _event(5, "succeeded", 28_000),
        )
    )

    stale_result = next(
        result
        for result in stale.trajectory.results
        if result.clause_id == "deployment.approval_fresh_at_deploy"
    )
    assert stale_result.status is MathStatus.VIOLATED
    assert stale_result.margin == Decimal("-40001")
    assert stale_result.counterexample is not None
    assert unauthenticated.trajectory.status is MathStatus.UNKNOWN
    assert unauthenticated.recommended_control is ControlLevel.HOLD


def test_missing_signed_evidence_fails_closed_and_self_approval_breaks_sod() -> None:
    missing = _verify(evidence=_evidence(include_rollback=False))
    self_approved_records = tuple(
        DeploymentEvidence(
            record.evidence_id,
            record.evidence_kind,
            "product-owner-3"
            if record.evidence_kind == "approval" and record.principal_id == "risk-officer-17"
            else record.principal_id,
            record.issued_at_ms,
            signature_verified=record.signature_verified,
        )
        for record in _evidence()
    )
    self_approved = _verify(evidence=self_approved_records)

    assert missing.evidence.status is MathStatus.UNKNOWN
    assert missing.recommended_control is ControlLevel.HOLD
    assert any(
        result.status is MathStatus.UNKNOWN and result.counterexample is not None
        for result in missing.evidence.results
    )
    sod = next(
        result
        for result in self_approved.evidence.results
        if result.clause_id == "deployment.separation_of_duties"
    )
    assert sod.status is MathStatus.VIOLATED
    assert sod.margin == Decimal("-1")
    assert sod.counterexample is not None


def test_change_window_canary_threshold_and_rollback_readiness_are_hard_bounds() -> None:
    assessment = _verify(
        snapshot=_snapshot(
            canary_error_rate_bps=26,
            deployment_at_ms=81_000,
            rollback_ready=False,
        ),
        trajectory=(
            _event(1, "change_approved", 70_000),
            _event(2, "canary_started", 75_000),
            _event(3, "canary_passed", 79_000),
            _event(4, "deployed", 81_000),
            _event(5, "succeeded", 82_000),
        ),
    )
    results = {result.clause_id: result for result in assessment.preflight.results}

    assert results["deployment.canary_error_rate"].margin == Decimal("-1")
    assert results["deployment.change_window"].margin == Decimal("-1000")
    assert results["deployment.rollback_ready"].margin == Decimal("-1")
    assert all(
        results[clause].status is MathStatus.VIOLATED and results[clause].counterexample is not None
        for clause in (
            "deployment.canary_error_rate",
            "deployment.change_window",
            "deployment.rollback_ready",
        )
    )


def test_incomplete_quorum_holds_and_late_terminal_outcome_is_rejected() -> None:
    one_approval = tuple(
        record for record in _evidence() if record.evidence_id != "sig:approval-sre"
    )
    incomplete_quorum = _verify(evidence=one_approval)
    late_terminal = _verify(
        trajectory=(
            _event(1, "change_approved", 20_000),
            _event(2, "canary_started", 21_000),
            _event(3, "canary_passed", 22_000),
            _event(4, "deployed", 25_000),
            _event(5, "succeeded", 60_001),
        )
    )

    quorum = next(
        result
        for result in incomplete_quorum.evidence.results
        if result.clause_id == "deployment.approval_quorum"
    )
    terminal = next(
        result
        for result in late_terminal.trajectory.results
        if result.clause_id == "deployment.bounded_terminal_outcome"
    )
    assert quorum.status is MathStatus.UNKNOWN
    assert quorum.margin is None
    assert quorum.counterexample is not None
    assert incomplete_quorum.recommended_control is ControlLevel.HOLD
    assert terminal.status is MathStatus.VIOLATED
    assert terminal.margin == Decimal("-5001")
    assert terminal.counterexample is not None
