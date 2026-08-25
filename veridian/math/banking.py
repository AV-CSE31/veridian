"""Synthetic bank-payment mathematics pack built from separable policy inputs.

This module is an industrial-shaped reference configuration, not a regulatory
standard or a substitute for a bank's approved risk, treasury, and accounting
models. Every threshold and disturbance is caller supplied and appears in the
result assumptions/evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from ._common import (
    ExactNumber,
    MathConfigurationError,
    MathReport,
    MathResult,
    MathStatus,
    exact_decimal,
    require_identifier,
)
from ._invariant import (
    BoundInvariant,
    ConservationInvariant,
    EqualityInvariant,
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


@dataclass(frozen=True)
class BankLiquidityStress:
    """One bank-reviewed unexpected-outflow scenario in currency minor units."""

    scenario_id: str
    unexpected_outflow_minor: ExactNumber
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", require_identifier(self.scenario_id, "scenario_id"))
        outflow = exact_decimal(self.unexpected_outflow_minor, "unexpected_outflow_minor")
        if outflow < 0:
            raise MathConfigurationError("unexpected outflow cannot be negative")
        object.__setattr__(self, "unexpected_outflow_minor", outflow)
        for evidence_id in self.evidence_ids:
            require_identifier(evidence_id, "evidence_id")


@dataclass(frozen=True)
class BankPaymentMathPolicy:
    """Explicit configuration for the synthetic critical-payment pack."""

    currency: str
    minor_unit_scale: int
    per_payment_limit_minor: ExactNumber
    liquidity_floor_minor: ExactNumber
    amount_perturbation_radius_minor: ExactNumber
    screening_max_age_ms: int
    terminal_outcome_max_delay_ms: int
    liquidity_stresses: tuple[BankLiquidityStress, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isalpha()
            or self.currency.upper() != self.currency
        ):
            raise MathConfigurationError("currency must be a three-letter uppercase code")
        if isinstance(self.minor_unit_scale, bool) or not isinstance(self.minor_unit_scale, int):
            raise MathConfigurationError("minor_unit_scale must be an integer")
        if not 0 <= self.minor_unit_scale <= 18:
            raise MathConfigurationError("minor_unit_scale must lie in [0, 18]")
        limit = exact_decimal(self.per_payment_limit_minor, "per_payment_limit_minor")
        floor = exact_decimal(self.liquidity_floor_minor, "liquidity_floor_minor")
        radius = exact_decimal(
            self.amount_perturbation_radius_minor, "amount_perturbation_radius_minor"
        )
        if limit <= 0:
            raise MathConfigurationError("per-payment limit must be positive")
        if floor < 0 or radius < 0:
            raise MathConfigurationError(
                "liquidity floor and perturbation radius cannot be negative"
            )
        object.__setattr__(self, "per_payment_limit_minor", limit)
        object.__setattr__(self, "liquidity_floor_minor", floor)
        object.__setattr__(self, "amount_perturbation_radius_minor", radius)
        for name in ("screening_max_age_ms", "terminal_outcome_max_delay_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MathConfigurationError(f"{name} must be a non-negative integer")
        if not self.liquidity_stresses:
            raise MathConfigurationError("at least one liquidity stress is required")
        ids = [stress.scenario_id for stress in self.liquidity_stresses]
        if len(ids) != len(set(ids)):
            raise MathConfigurationError("liquidity stress identifiers must be unique")

    @property
    def minor_unit(self) -> str:
        if self.minor_unit_scale == 2:
            return f"{self.currency}-cent"
        return f"{self.currency}-minor-1e-{self.minor_unit_scale}"


@dataclass(frozen=True)
class BankPaymentAssessment:
    """Combined action, robustness, and completion mathematics for one payment."""

    accounting: MathReport
    liquidity: MathResult
    amount_control: MathResult
    trajectory: MathReport
    assumptions: tuple[str, ...]

    @property
    def results(self) -> tuple[MathResult, ...]:
        return (
            *self.accounting.results,
            self.liquidity,
            self.amount_control,
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


@dataclass(frozen=True)
class BankPaymentMathSuite:
    """Deep public interface for the configured critical-payment mathematics."""

    policy: BankPaymentMathPolicy
    accounting: InvariantVerifier
    liquidity: BarrierVerifier
    amount_control: MetamorphicVerifier
    trajectory: TrajectoryVerifier

    def verify(
        self,
        *,
        accounting_snapshot: Mapping[str, ExactNumber],
        liquidity_state: Mapping[str, ExactNumber],
        liquidity_control: Mapping[str, ExactNumber],
        baseline_control: ControlLevel,
        amount_perturbations: tuple[ControlPerturbation, ...],
        trajectory: tuple[TrajectoryEvent, ...],
        trajectory_complete: bool,
        evidence_ids: tuple[str, ...] = (),
    ) -> BankPaymentAssessment:
        """Verify one bank-payment snapshot through the pack's public seams."""
        return BankPaymentAssessment(
            accounting=self.accounting.verify(
                accounting_snapshot,
                evidence_ids=evidence_ids,
            ),
            liquidity=self.liquidity.verify(
                state=liquidity_state,
                control=liquidity_control,
                evidence_ids=evidence_ids,
            ),
            amount_control=self.amount_control.verify(
                baseline_control,
                amount_perturbations,
            ),
            trajectory=self.trajectory.verify(
                trajectory,
                complete=trajectory_complete,
                evidence_ids=evidence_ids,
            ),
            assumptions=(
                "synthetic policy",
                "thresholds and scenarios require bank risk, treasury, and accounting approval",
                f"currency={self.policy.currency}; minor_unit_scale={self.policy.minor_unit_scale}",
            ),
        )


def build_bank_payment_math(policy: BankPaymentMathPolicy) -> BankPaymentMathSuite:
    """Build the deterministic verifier suite from an explicit bank policy."""
    unit = policy.minor_unit
    accounting_invariants = cast(
        tuple[InvariantClause, ...],
        (
            ConservationInvariant(
                invariant_id="bank.posting.conservation",
                inflows=("source_debit_minor",),
                outflows=("beneficiary_credit_minor", "fee_minor"),
                unit=unit,
                assumptions=("source debit includes the transfer fee",),
            ),
            EqualityInvariant(
                invariant_id="bank.transfer.matches_credit",
                left=LinearExpression.field("transfer_minor", unit=unit),
                right=LinearExpression.field("beneficiary_credit_minor", unit=unit),
            ),
            BoundInvariant(
                invariant_id="bank.per_payment_limit",
                expression=LinearExpression.field("transfer_minor", unit=unit),
                lower=1,
                upper=policy.per_payment_limit_minor,
            ),
            BoundInvariant(
                invariant_id="bank.nonnegative_fee",
                expression=LinearExpression.field("fee_minor", unit=unit),
                lower=0,
            ),
            BoundInvariant(
                invariant_id="bank.observed_liquidity_floor",
                expression=LinearExpression.field("post_available_minor", unit=unit),
                lower=policy.liquidity_floor_minor,
            ),
        ),
    )
    accounting = InvariantVerifier(accounting_invariants)
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
                    unit=unit,
                ),
            ),
        )
    )
    liquidity = BarrierVerifier(
        clause_id="bank.robust_liquidity_barrier",
        transition=transition,
        barriers=(
            AffineBarrier(
                "bank.minimum_operating_liquidity",
                LinearExpression.field("available_next_minor", unit=unit),
                minimum=policy.liquidity_floor_minor,
            ),
        ),
        disturbances=tuple(
            DisturbanceScenario(
                stress.scenario_id,
                (
                    VariableValue(
                        "unexpected_outflow_minor",
                        stress.unexpected_outflow_minor,
                    ),
                ),
                evidence_ids=stress.evidence_ids,
            )
            for stress in policy.liquidity_stresses
        ),
        assumptions=("available balance model excludes undeclared holds and settlement effects",),
    )
    amount_control = MetamorphicVerifier(
        clause_id="bank.amount_control_monotonicity",
        relation=MetamorphicRelation.CONTROL_NON_DECREASING,
        norm=VectorNorm.L1,
        radius=exact_decimal(
            policy.amount_perturbation_radius_minor,
            "amount_perturbation_radius_minor",
        ),
        input_unit=unit,
        assumptions=("increasing amount is declared risk-relevant",),
    )
    accepting = frozenset(
        {"settled", "failed", "compensated", "expired", "reconciliation_hold", "revoked"}
    )
    state_machine = StateMachineMonitor(
        rule_id="bank.payment_state_machine",
        initial_state="proposed",
        transitions=(
            StateTransition("proposed", "screened", "screened"),
            StateTransition("proposed", "revoked", "revoked"),
            StateTransition("screened", "authorized", "authorized"),
            StateTransition("screened", "revoked", "revoked"),
            StateTransition("authorized", "permit_redeemed", "permitted"),
            StateTransition("authorized", "expired", "expired"),
            StateTransition("authorized", "revoked", "revoked"),
            StateTransition("permitted", "dispatched", "in_flight"),
            StateTransition("permitted", "expired", "expired"),
            StateTransition("permitted", "revoked", "revoked"),
            StateTransition("in_flight", "settled", "settled"),
            StateTransition("in_flight", "failed", "failed"),
            StateTransition("in_flight", "reconciliation_hold", "reconciliation_hold"),
            StateTransition("failed", "compensated", "compensated"),
        ),
        accepting_states=accepting,
    )
    trajectory_rules = cast(
        tuple[TrajectoryRule, ...],
        (
            state_machine,
            PrecedenceRule("bank.authorization_before_dispatch", "authorized", "dispatched"),
            AtMostOnceRule("bank.single_use_permit", "permit_redeemed"),
            FreshnessRule(
                "bank.screening_fresh_at_dispatch",
                evidence_event="screened",
                consuming_event="dispatched",
                max_age_ms=policy.screening_max_age_ms,
            ),
            ForbiddenAfterRule("bank.revoked_never_dispatches", "revoked", "dispatched"),
            TerminalOutcomeRule(
                "bank.authorized_eventually_terminal",
                start_event="authorized",
                terminal_events=frozenset(
                    {"settled", "failed", "compensated", "expired", "reconciliation_hold"}
                ),
                max_delay_ms=policy.terminal_outcome_max_delay_ms,
            ),
        ),
    )
    trajectory = TrajectoryVerifier(trajectory_rules)
    return BankPaymentMathSuite(
        policy=policy,
        accounting=accounting,
        liquidity=liquidity,
        amount_control=amount_control,
        trajectory=trajectory,
    )
