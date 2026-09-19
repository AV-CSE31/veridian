"""Model-relative mathematics for production deployment change control.

The pack combines deterministic evidence checks, exact release invariants, a
finite one-step error-budget barrier, metamorphic control checks, and bounded
trajectory monitors.  It proves only the configured clauses over caller-
supplied evidence and disturbances; it does not prove universal service safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from ._common import (
    ExactNumber,
    MathConfigurationError,
    MathCounterexample,
    MathInputError,
    MathReport,
    MathResult,
    MathStatus,
    NumericOperand,
    ReasonCode,
    exact_decimal,
    require_identifier,
)
from ._invariant import (
    BoundInvariant,
    InvariantClause,
    InvariantVerifier,
    LinearExpression,
    LinearTerm,
)
from ._robustness import (
    ControlLevel,
    ControlPerturbation,
    MetamorphicRelation,
    MetamorphicVerifier,
    VectorNorm,
)
from ._stability import (
    AffineBarrier,
    AffineTransition,
    BarrierVerifier,
    DisturbanceScenario,
    StateEquation,
    VariableValue,
)
from ._trajectory import (
    AtMostOnceRule,
    ForbiddenAfterRule,
    FreshnessRule,
    PrecedenceRule,
    StateMachineMonitor,
    StateTransition,
    TerminalOutcomeRule,
    TrajectoryEvent,
    TrajectoryRule,
    TrajectoryVerifier,
)

_APPROVAL_KIND = "approval"
_MANDATORY_EVIDENCE_KINDS = frozenset(
    {
        "artifact-attestation",
        "canary-observation",
        "change-ticket",
        "rollback-attestation",
        "slo-snapshot",
    }
)


def _require_non_negative_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MathConfigurationError(f"{field_name} must be an integer")
    if value < 0:
        raise MathConfigurationError(f"{field_name} cannot be negative")
    return value


@dataclass(frozen=True)
class DeploymentRiskDisturbance:
    """One explicitly enumerated additional error-budget burn scenario."""

    scenario_id: str
    unexpected_budget_burn_bps: ExactNumber
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", require_identifier(self.scenario_id, "scenario_id"))
        burn = exact_decimal(self.unexpected_budget_burn_bps, "unexpected_budget_burn_bps")
        if burn < 0:
            raise MathConfigurationError("unexpected error-budget burn cannot be negative")
        object.__setattr__(self, "unexpected_budget_burn_bps", burn)
        for evidence_id in self.evidence_ids:
            require_identifier(evidence_id, "evidence_id")


@dataclass(frozen=True)
class DeploymentMathPolicy:
    """Bank-approved thresholds for one high-risk service change window."""

    required_approval_quorum: int
    required_evidence_kinds: frozenset[str]
    evidence_max_age_ms: int
    change_window_start_ms: int
    change_window_end_ms: int
    maximum_canary_error_rate_bps: ExactNumber
    minimum_error_budget_reserve_bps: ExactNumber
    risk_perturbation_radius_bps: ExactNumber
    terminal_outcome_max_delay_ms: int
    disturbances: tuple[DeploymentRiskDisturbance, ...]

    def __post_init__(self) -> None:
        quorum = _require_non_negative_int(
            self.required_approval_quorum, "required_approval_quorum"
        )
        if quorum == 0:
            raise MathConfigurationError("required_approval_quorum must be positive")
        if not isinstance(self.required_evidence_kinds, frozenset):
            raise MathConfigurationError("required_evidence_kinds must be a frozenset")
        if not self.required_evidence_kinds:
            raise MathConfigurationError("at least one non-approval evidence kind is required")
        for kind in self.required_evidence_kinds:
            require_identifier(kind, "required evidence kind")
        if _APPROVAL_KIND in self.required_evidence_kinds:
            raise MathConfigurationError("approval evidence is configured through the quorum")
        missing_mandatory = _MANDATORY_EVIDENCE_KINDS.difference(self.required_evidence_kinds)
        if missing_mandatory:
            missing = ", ".join(sorted(missing_mandatory))
            raise MathConfigurationError(
                f"required evidence kinds omit mandatory controls: {missing}"
            )

        _require_non_negative_int(self.evidence_max_age_ms, "evidence_max_age_ms")
        start = _require_non_negative_int(self.change_window_start_ms, "change_window_start_ms")
        end = _require_non_negative_int(self.change_window_end_ms, "change_window_end_ms")
        if start >= end:
            raise MathConfigurationError("change window start must precede its end")
        _require_non_negative_int(
            self.terminal_outcome_max_delay_ms,
            "terminal_outcome_max_delay_ms",
        )

        canary = exact_decimal(
            self.maximum_canary_error_rate_bps,
            "maximum_canary_error_rate_bps",
        )
        reserve = exact_decimal(
            self.minimum_error_budget_reserve_bps,
            "minimum_error_budget_reserve_bps",
        )
        radius = exact_decimal(
            self.risk_perturbation_radius_bps,
            "risk_perturbation_radius_bps",
        )
        if canary < 0 or canary > 10_000:
            raise MathConfigurationError("maximum canary error rate must lie in [0, 10000] bps")
        if reserve < 0 or reserve > 10_000:
            raise MathConfigurationError("minimum error-budget reserve must lie in [0, 10000] bps")
        if radius < 0:
            raise MathConfigurationError("risk perturbation radius cannot be negative")
        object.__setattr__(self, "maximum_canary_error_rate_bps", canary)
        object.__setattr__(self, "minimum_error_budget_reserve_bps", reserve)
        object.__setattr__(self, "risk_perturbation_radius_bps", radius)

        if not self.disturbances:
            raise MathConfigurationError("at least one deployment risk disturbance is required")
        scenario_ids = [scenario.scenario_id for scenario in self.disturbances]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise MathConfigurationError("deployment disturbance identifiers must be unique")


@dataclass(frozen=True)
class DeploymentEvidence:
    """Caller-supplied reference to evidence verified by a cryptographic boundary.

    ``signature_verified`` is an input assertion, not signature verification in
    this math module.  The suite treats false, stale, future, or absent evidence
    as UNKNOWN so an assurance layer can map it to HOLD.
    """

    evidence_id: str
    evidence_kind: str
    principal_id: str
    issued_at_ms: int
    signature_verified: bool

    def __post_init__(self) -> None:
        for field_name in ("evidence_id", "evidence_kind", "principal_id"):
            object.__setattr__(
                self,
                field_name,
                require_identifier(getattr(self, field_name), field_name),
            )
        if isinstance(self.issued_at_ms, bool) or not isinstance(self.issued_at_ms, int):
            raise MathInputError("evidence issued_at_ms must be an integer")
        if self.issued_at_ms < 0:
            raise MathInputError("evidence issued_at_ms cannot be negative")
        if not isinstance(self.signature_verified, bool):
            raise MathInputError("signature_verified must be boolean")


@dataclass(frozen=True)
class DeploymentControlSnapshot:
    """Exact measurements bound to the release decision and observed deploy."""

    remaining_error_budget_bps: ExactNumber
    projected_deployment_burn_bps: ExactNumber
    canary_error_rate_bps: ExactNumber
    deployment_at_ms: int
    rollback_ready: bool

    def __post_init__(self) -> None:
        for field_name in (
            "remaining_error_budget_bps",
            "projected_deployment_burn_bps",
            "canary_error_rate_bps",
        ):
            value = exact_decimal(getattr(self, field_name), field_name)
            if value < 0:
                raise MathInputError(f"{field_name} cannot be negative")
            object.__setattr__(self, field_name, value)
        if isinstance(self.deployment_at_ms, bool) or not isinstance(self.deployment_at_ms, int):
            raise MathInputError("deployment_at_ms must be an integer")
        if self.deployment_at_ms < 0:
            raise MathInputError("deployment_at_ms cannot be negative")
        if not isinstance(self.rollback_ready, bool):
            raise MathInputError("rollback_ready must be boolean")

    def to_preflight_mapping(self) -> dict[str, ExactNumber]:
        """Return the exact numeric projection consumed by invariant clauses."""
        return {
            "canary_error_rate_bps": self.canary_error_rate_bps,
            "deployment_at_ms": self.deployment_at_ms,
            "rollback_ready": int(self.rollback_ready),
        }


def _evidence_ids(evidence: tuple[DeploymentEvidence, ...]) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in evidence)


def _evidence_result(
    *,
    clause_id: str,
    status: MathStatus,
    derivation: str,
    margin: Decimal | None,
    operands: tuple[NumericOperand, ...],
    evidence_ids: tuple[str, ...],
    counterexample_summary: str | None = None,
) -> MathResult:
    if status is MathStatus.SATISFIED:
        reason_code = ReasonCode.BOUNDS_SATISFIED
    elif status is MathStatus.UNKNOWN:
        reason_code = ReasonCode.INPUT_MISSING
    else:
        reason_code = ReasonCode.BOUNDS_VIOLATED
    return MathResult(
        clause_id=clause_id,
        status=status,
        reason_code=reason_code,
        derivation=derivation,
        operands=operands,
        unit="control",
        tolerance=Decimal(0),
        margin=margin,
        assumptions=(
            "evidence identifiers refer to canonical artifacts",
            "signature verification is performed by a trusted cryptographic boundary",
            "principal and role mappings are supplied by the deployment identity authority",
        ),
        evidence_ids=evidence_ids,
        counterexample=(
            MathCounterexample(clause_id, counterexample_summary, operands)
            if counterexample_summary is not None
            else None
        ),
    )


def _age(record: DeploymentEvidence, as_of_ms: int) -> int:
    return as_of_ms - record.issued_at_ms


def _usable(record: DeploymentEvidence, as_of_ms: int, max_age_ms: int) -> bool:
    age = _age(record, as_of_ms)
    return record.signature_verified and 0 <= age <= max_age_ms


def _verify_evidence(
    *,
    policy: DeploymentMathPolicy,
    evidence: tuple[DeploymentEvidence, ...],
    requester_principal_id: str,
    deployer_principal_id: str,
    as_of_ms: int,
) -> MathReport:
    evidence_ids = _evidence_ids(evidence)
    by_kind: dict[str, list[DeploymentEvidence]] = {}
    for item in evidence:
        by_kind.setdefault(item.evidence_kind, []).append(item)
    id_count = len(set(evidence_ids))
    ids_operands = (
        NumericOperand("supplied_evidence_count", Decimal(len(evidence)), "evidence"),
        NumericOperand("unique_evidence_id_count", Decimal(id_count), "evidence"),
    )
    missing_kinds = sorted(policy.required_evidence_kinds.difference(by_kind))
    unusable = tuple(
        item
        for item in evidence
        if item.evidence_kind in policy.required_evidence_kinds
        and not _usable(item, as_of_ms, policy.evidence_max_age_ms)
    )
    if id_count != len(evidence):
        authentication = _evidence_result(
            clause_id="deployment.required_evidence",
            status=MathStatus.VIOLATED,
            derivation="evidence identifiers must be unique",
            margin=Decimal(id_count - len(evidence)),
            operands=ids_operands,
            evidence_ids=evidence_ids,
            counterexample_summary="duplicate evidence identifiers make provenance ambiguous",
        )
    elif missing_kinds:
        authentication = _evidence_result(
            clause_id="deployment.required_evidence",
            status=MathStatus.UNKNOWN,
            derivation="every required evidence kind must have a fresh authenticated record",
            margin=None,
            operands=ids_operands,
            evidence_ids=evidence_ids,
            counterexample_summary=f"missing required evidence kinds: {', '.join(missing_kinds)}",
        )
    elif unusable:
        first = min(unusable, key=lambda item: (item.evidence_kind, item.evidence_id))
        authentication = _evidence_result(
            clause_id="deployment.required_evidence",
            status=MathStatus.UNKNOWN,
            derivation="every required evidence record must be authenticated and fresh",
            margin=None,
            operands=ids_operands
            + (
                NumericOperand(
                    "unusable_evidence_age",
                    Decimal(_age(first, as_of_ms)),
                    "millisecond",
                ),
            ),
            evidence_ids=evidence_ids,
            counterexample_summary=(
                f"evidence {first.evidence_id!r} is unauthenticated, stale, or future-dated"
            ),
        )
    else:
        worst_freshness = min(
            (
                policy.evidence_max_age_ms - _age(item, as_of_ms)
                for item in evidence
                if item.evidence_kind in policy.required_evidence_kinds
            ),
            default=policy.evidence_max_age_ms,
        )
        authentication = _evidence_result(
            clause_id="deployment.required_evidence",
            status=MathStatus.SATISFIED,
            derivation="all configured evidence kinds have authenticated records in the freshness window",
            margin=Decimal(worst_freshness),
            operands=ids_operands,
            evidence_ids=evidence_ids,
        )

    approvals = tuple(by_kind.get(_APPROVAL_KIND, ()))
    usable_approvals = tuple(
        item for item in approvals if _usable(item, as_of_ms, policy.evidence_max_age_ms)
    )
    distinct_approvers = frozenset(item.principal_id for item in usable_approvals)
    quorum_margin = len(distinct_approvers) - policy.required_approval_quorum
    quorum_operands = (
        NumericOperand("approval_record_count", Decimal(len(approvals)), "approval"),
        NumericOperand(
            "distinct_authenticated_approver_count",
            Decimal(len(distinct_approvers)),
            "principal",
        ),
        NumericOperand(
            "required_approval_quorum",
            Decimal(policy.required_approval_quorum),
            "principal",
        ),
    )
    if len(approvals) < policy.required_approval_quorum or any(
        not _usable(item, as_of_ms, policy.evidence_max_age_ms) for item in approvals
    ):
        quorum = _evidence_result(
            clause_id="deployment.approval_quorum",
            status=MathStatus.UNKNOWN,
            derivation="a complete fresh authenticated approval set is required",
            margin=None,
            operands=quorum_operands,
            evidence_ids=evidence_ids,
            counterexample_summary="approval evidence is missing, unauthenticated, stale, or future-dated",
        )
    elif quorum_margin < 0:
        quorum = _evidence_result(
            clause_id="deployment.approval_quorum",
            status=MathStatus.VIOLATED,
            derivation="distinct authenticated approvers must meet the configured quorum",
            margin=Decimal(quorum_margin),
            operands=quorum_operands,
            evidence_ids=evidence_ids,
            counterexample_summary="duplicate principals do not count as independent approvals",
        )
    else:
        quorum = _evidence_result(
            clause_id="deployment.approval_quorum",
            status=MathStatus.SATISFIED,
            derivation="distinct authenticated approvers meet the configured quorum",
            margin=Decimal(quorum_margin),
            operands=quorum_operands,
            evidence_ids=evidence_ids,
        )

    role_overlap = distinct_approvers.intersection({requester_principal_id, deployer_principal_id})
    same_operator = requester_principal_id == deployer_principal_id
    sod_operands = (
        NumericOperand("conflicting_principal_count", Decimal(len(role_overlap)), "principal"),
        NumericOperand("requester_is_deployer", Decimal(int(same_operator)), "boolean"),
    )
    if not usable_approvals:
        sod = _evidence_result(
            clause_id="deployment.separation_of_duties",
            status=MathStatus.UNKNOWN,
            derivation="requester, deployer, and authenticated approvers must be separable",
            margin=None,
            operands=sod_operands,
            evidence_ids=evidence_ids,
            counterexample_summary="no usable approval evidence establishes separation of duties",
        )
    elif role_overlap or same_operator:
        conflicts = len(role_overlap) + int(same_operator)
        sod = _evidence_result(
            clause_id="deployment.separation_of_duties",
            status=MathStatus.VIOLATED,
            derivation="requester and deployer must be distinct and neither may approve",
            margin=Decimal(-conflicts),
            operands=sod_operands,
            evidence_ids=evidence_ids,
            counterexample_summary="one or more principals occupy conflicting change-control roles",
        )
    else:
        sod = _evidence_result(
            clause_id="deployment.separation_of_duties",
            status=MathStatus.SATISFIED,
            derivation="requester, deployer, and authenticated approvers are distinct",
            margin=Decimal(1),
            operands=sod_operands,
            evidence_ids=evidence_ids,
        )
    return MathReport((authentication, quorum, sod))


def _deployment_time_binding(
    snapshot: DeploymentControlSnapshot,
    trajectory: tuple[TrajectoryEvent, ...],
    evidence_ids: tuple[str, ...],
) -> MathResult:
    deployments = tuple(event for event in trajectory if event.event_type == "deployed")
    operands = (
        NumericOperand("deployment_event_count", Decimal(len(deployments)), "event"),
        NumericOperand(
            "declared_deployment_at",
            Decimal(snapshot.deployment_at_ms),
            "millisecond",
        ),
    )
    if not deployments:
        return MathResult(
            clause_id="deployment.observed_time_binding",
            status=MathStatus.UNKNOWN,
            reason_code=ReasonCode.INPUT_MISSING,
            derivation="an authenticated deployment event must bind the measured control snapshot",
            operands=operands,
            unit="millisecond",
            tolerance=Decimal(0),
            margin=None,
            assumptions=("the deployment event timestamp is bound by its signed event evidence",),
            evidence_ids=evidence_ids,
            counterexample=MathCounterexample(
                "deployment.observed_time_binding",
                "no deployment event binds the declared deployment timestamp",
                operands,
            ),
        )
    observed = deployments[0].occurred_at_ms
    difference = abs(snapshot.deployment_at_ms - observed)
    satisfied = len(deployments) == 1 and difference == 0 and deployments[0].authenticated
    full_operands = operands + (
        NumericOperand("observed_deployment_at", Decimal(observed), "millisecond"),
        NumericOperand("timestamp_difference", Decimal(difference), "millisecond"),
    )
    return MathResult(
        clause_id="deployment.observed_time_binding",
        status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
        reason_code=(ReasonCode.EQUALITY_SATISFIED if satisfied else ReasonCode.EQUALITY_VIOLATED),
        derivation="exactly one authenticated deployment event must equal deployment_at_ms",
        operands=full_operands,
        unit="millisecond",
        tolerance=Decimal(0),
        margin=Decimal(0) if satisfied else Decimal(-max(1, difference)),
        assumptions=("event time and snapshot time use the same trusted clock domain",),
        evidence_ids=tuple(
            dict.fromkeys((*evidence_ids, *(event.evidence_id for event in deployments)))
        ),
        counterexample=(
            None
            if satisfied
            else MathCounterexample(
                "deployment.observed_time_binding",
                "deployment count, authentication, or timestamp does not match the snapshot",
                full_operands,
            )
        ),
    )


@dataclass(frozen=True)
class DeploymentAssessment:
    """Combined evidence, invariant, disturbance, and lifecycle assessment."""

    evidence: MathReport
    preflight: MathReport
    error_budget: MathResult
    risk_control: MathResult
    trajectory: MathReport
    assumptions: tuple[str, ...]

    @property
    def results(self) -> tuple[MathResult, ...]:
        return (
            *self.evidence.results,
            *self.preflight.results,
            self.error_budget,
            self.risk_control,
            *self.trajectory.results,
        )

    @property
    def status(self) -> MathStatus:
        statuses = {result.status for result in self.results}
        if MathStatus.VIOLATED in statuses:
            return MathStatus.VIOLATED
        if MathStatus.ERROR in statuses:
            return MathStatus.ERROR
        if MathStatus.UNKNOWN in statuses:
            return MathStatus.UNKNOWN
        return MathStatus.SATISFIED

    @property
    def recommended_control(self) -> ControlLevel:
        """Map four-valued math to the fail-closed deployment control lattice."""
        if self.status is MathStatus.SATISFIED:
            return ControlLevel.ALLOW
        if self.status is MathStatus.VIOLATED:
            return ControlLevel.DENY
        return ControlLevel.HOLD


@dataclass(frozen=True)
class DeploymentMathSuite:
    """Deep interface for one configured production change-control model."""

    policy: DeploymentMathPolicy
    preflight: InvariantVerifier
    error_budget: BarrierVerifier
    risk_control: MetamorphicVerifier
    trajectory: TrajectoryVerifier

    def verify(
        self,
        *,
        snapshot: DeploymentControlSnapshot,
        evidence: tuple[DeploymentEvidence, ...],
        requester_principal_id: str,
        deployer_principal_id: str,
        baseline_control: ControlLevel,
        risk_perturbations: tuple[ControlPerturbation, ...],
        trajectory: tuple[TrajectoryEvent, ...],
        trajectory_complete: bool,
    ) -> DeploymentAssessment:
        """Evaluate one change without executing or authorizing the deployment."""
        require_identifier(requester_principal_id, "requester_principal_id")
        require_identifier(deployer_principal_id, "deployer_principal_id")
        evidence_ids = _evidence_ids(evidence)
        evidence_report = _verify_evidence(
            policy=self.policy,
            evidence=evidence,
            requester_principal_id=requester_principal_id,
            deployer_principal_id=deployer_principal_id,
            as_of_ms=snapshot.deployment_at_ms,
        )
        invariant_report = self.preflight.verify(
            snapshot.to_preflight_mapping(),
            evidence_ids=evidence_ids,
        )
        preflight = MathReport(
            (
                *invariant_report.results,
                _deployment_time_binding(snapshot, trajectory, evidence_ids),
            )
        )
        return DeploymentAssessment(
            evidence=evidence_report,
            preflight=preflight,
            error_budget=self.error_budget.verify(
                state={"remaining_error_budget_bps": snapshot.remaining_error_budget_bps},
                control={"projected_deployment_burn_bps": snapshot.projected_deployment_burn_bps},
                evidence_ids=evidence_ids,
            ),
            risk_control=self.risk_control.verify(baseline_control, risk_perturbations),
            trajectory=self.trajectory.verify(
                trajectory,
                complete=trajectory_complete,
                evidence_ids=evidence_ids,
            ),
            assumptions=(
                "model-relative deployment verification",
                "policy thresholds, evidence trust, and disturbance coverage require bank approval",
                "this finite deterministic pack does not establish universal production safety",
            ),
        )


def build_deployment_math(policy: DeploymentMathPolicy) -> DeploymentMathSuite:
    """Build an immutable deterministic suite from an explicit change policy."""
    unit = "basis-point"
    preflight = InvariantVerifier(
        cast(
            tuple[InvariantClause, ...],
            (
                BoundInvariant(
                    invariant_id="deployment.canary_error_rate",
                    expression=LinearExpression.field("canary_error_rate_bps", unit=unit),
                    lower=0,
                    upper=policy.maximum_canary_error_rate_bps,
                    assumptions=("canary telemetry uses the policy-defined request population",),
                ),
                BoundInvariant(
                    invariant_id="deployment.change_window",
                    expression=LinearExpression.field("deployment_at_ms", unit="millisecond"),
                    lower=policy.change_window_start_ms,
                    upper=policy.change_window_end_ms,
                    assumptions=(
                        "change-window bounds and event time share a trusted clock domain",
                    ),
                ),
                BoundInvariant(
                    invariant_id="deployment.rollback_ready",
                    expression=LinearExpression.field("rollback_ready", unit="boolean"),
                    lower=1,
                    upper=1,
                    assumptions=("rollback readiness is backed by authenticated evidence",),
                ),
            ),
        )
    )
    error_budget = BarrierVerifier(
        clause_id="deployment.error_budget_barrier",
        transition=AffineTransition(
            (
                StateEquation(
                    "remaining_error_budget_next_bps",
                    LinearExpression(
                        (
                            LinearTerm("remaining_error_budget_bps", Decimal(1)),
                            LinearTerm("projected_deployment_burn_bps", Decimal(-1)),
                            LinearTerm("unexpected_budget_burn_bps", Decimal(-1)),
                        ),
                        unit=unit,
                    ),
                ),
            )
        ),
        barriers=(
            AffineBarrier(
                "deployment.minimum_error_budget_reserve",
                LinearExpression.field("remaining_error_budget_next_bps", unit=unit),
                minimum=policy.minimum_error_budget_reserve_bps,
                assumptions=("error-budget burn is additive over the one-step release horizon",),
            ),
        ),
        disturbances=tuple(
            DisturbanceScenario(
                scenario.scenario_id,
                (
                    VariableValue(
                        "unexpected_budget_burn_bps",
                        scenario.unexpected_budget_burn_bps,
                    ),
                ),
                evidence_ids=scenario.evidence_ids,
            )
            for scenario in policy.disturbances
        ),
        assumptions=("remaining budget and projected burn use the same SLO accounting window",),
    )
    risk_control = MetamorphicVerifier(
        clause_id="deployment.risk_control_monotonicity",
        relation=MetamorphicRelation.CONTROL_NON_DECREASING,
        norm=VectorNorm.L1,
        radius=exact_decimal(policy.risk_perturbation_radius_bps, "risk_perturbation_radius_bps"),
        input_unit=unit,
        assumptions=("supplied transformations are declared risk-increasing by change policy",),
    )
    state_machine = StateMachineMonitor(
        rule_id="deployment.state_machine",
        initial_state="planned",
        transitions=(
            StateTransition("planned", "change_approved", "approved"),
            StateTransition("planned", "revoked", "revoked"),
            StateTransition("approved", "canary_started", "canary"),
            StateTransition("approved", "revoked", "revoked"),
            StateTransition("canary", "canary_passed", "ready"),
            StateTransition("canary", "canary_failed", "rolled_back"),
            StateTransition("canary", "revoked", "revoked"),
            StateTransition("ready", "deployed", "in_progress"),
            StateTransition("ready", "revoked", "revoked"),
            StateTransition("in_progress", "succeeded", "succeeded"),
            StateTransition("in_progress", "rolled_back", "rolled_back"),
            StateTransition("in_progress", "failed", "failed"),
        ),
        accepting_states=frozenset({"succeeded", "rolled_back", "failed", "revoked"}),
    )
    trajectory = TrajectoryVerifier(
        cast(
            tuple[TrajectoryRule, ...],
            (
                state_machine,
                PrecedenceRule(
                    "deployment.approval_before_deploy",
                    "change_approved",
                    "deployed",
                ),
                PrecedenceRule(
                    "deployment.canary_before_deploy",
                    "canary_passed",
                    "deployed",
                ),
                AtMostOnceRule("deployment.at_most_once", "deployed"),
                FreshnessRule(
                    "deployment.approval_fresh_at_deploy",
                    evidence_event="change_approved",
                    consuming_event="deployed",
                    max_age_ms=policy.evidence_max_age_ms,
                ),
                ForbiddenAfterRule(
                    "deployment.revocation_is_final",
                    trigger_event="revoked",
                    forbidden_event="deployed",
                ),
                TerminalOutcomeRule(
                    "deployment.bounded_terminal_outcome",
                    start_event="deployed",
                    terminal_events=frozenset({"succeeded", "rolled_back", "failed"}),
                    max_delay_ms=policy.terminal_outcome_max_delay_ms,
                ),
            ),
        )
    )
    return DeploymentMathSuite(
        policy=policy,
        preflight=preflight,
        error_budget=error_budget,
        risk_control=risk_control,
        trajectory=trajectory,
    )


__all__ = [
    "DeploymentAssessment",
    "DeploymentControlSnapshot",
    "DeploymentEvidence",
    "DeploymentMathPolicy",
    "DeploymentMathSuite",
    "DeploymentRiskDisturbance",
    "build_deployment_math",
]
