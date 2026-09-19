"""Bounded affine barrier and model-relative risk-potential verification.

The checks here are finite, one-step model evaluations. In particular,
``RiskStabilityVerifier`` does not claim universal stability of an AI agent or
of an unmodelled closed-loop system.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from ._common import (
    ExactNumber,
    MathConfigurationError,
    MathCounterexample,
    MathInputError,
    MathResult,
    MathStatus,
    NumericOperand,
    ReasonCode,
    decimal_arithmetic,
    exact_decimal,
    require_identifier,
)
from ._invariant import LinearExpression


@dataclass(frozen=True)
class VariableValue:
    """One exact named input in a finite disturbance scenario."""

    name: str
    value: ExactNumber

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "variable name"))
        object.__setattr__(self, "value", exact_decimal(self.value, self.name))


@dataclass(frozen=True)
class DisturbanceScenario:
    """A named member of the explicitly enumerated disturbance set."""

    scenario_id: str
    values: tuple[VariableValue, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", require_identifier(self.scenario_id, "scenario_id"))
        names = [value.name for value in self.values]
        if len(names) != len(set(names)):
            raise MathConfigurationError("disturbance variable names must be unique")
        for evidence_id in self.evidence_ids:
            require_identifier(evidence_id, "evidence_id")

    def to_mapping(self) -> dict[str, ExactNumber]:
        return {value.name: value.value for value in self.values}


@dataclass(frozen=True)
class StateEquation:
    """One next-state coordinate in an affine transition model."""

    target: str
    expression: LinearExpression

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", require_identifier(self.target, "state target"))


@dataclass(frozen=True)
class AffineTransition:
    """Exact sparse transition ``x' = f(x, u, w)`` over named variables."""

    equations: tuple[StateEquation, ...]

    def __post_init__(self) -> None:
        if not self.equations:
            raise MathConfigurationError("an affine transition requires an equation")
        targets = [equation.target for equation in self.equations]
        if len(targets) != len(set(targets)):
            raise MathConfigurationError("next-state equation targets must be unique")

    def evaluate(
        self,
        state: Mapping[str, ExactNumber],
        control: Mapping[str, ExactNumber],
        disturbance: Mapping[str, ExactNumber],
    ) -> dict[str, Decimal]:
        variables: dict[str, Decimal] = {}
        normalized_state: dict[str, Decimal] = {}
        for source_name, source in (
            ("state", state),
            ("control", control),
            ("disturbance", disturbance),
        ):
            overlap = set(variables).intersection(source)
            if overlap:
                names = ", ".join(sorted(overlap))
                raise MathInputError(f"{source_name} variables overlap existing variables: {names}")
            for name, value in source.items():
                require_identifier(name, f"{source_name} variable")
                normalized = exact_decimal(value, name)
                variables[name] = normalized
                if source_name == "state":
                    normalized_state[name] = normalized
        # Unassigned state coordinates are unchanged (explicit frame semantics).
        next_state = dict(normalized_state)
        for equation in self.equations:
            value, _ = equation.expression.evaluate(variables)
            next_state[equation.target] = value
        return next_state


@dataclass(frozen=True)
class AffineBarrier:
    """Declare the closed safe half-space ``h(x) = expression - minimum >= 0``."""

    barrier_id: str
    expression: LinearExpression
    minimum: ExactNumber = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "barrier_id", require_identifier(self.barrier_id, "barrier_id"))
        object.__setattr__(self, "minimum", exact_decimal(self.minimum, "barrier minimum"))

    def margin(self, next_state: Mapping[str, ExactNumber]) -> tuple[Decimal, Decimal]:
        value, _ = self.expression.evaluate(next_state)
        with decimal_arithmetic():
            return value - exact_decimal(self.minimum, "barrier minimum"), value


def _require_unique_scenarios(scenarios: tuple[DisturbanceScenario, ...]) -> None:
    if not scenarios:
        raise MathConfigurationError("at least one bounded disturbance scenario is required")
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise MathConfigurationError("disturbance scenario identifiers must be unique")


def _merged_evidence(base: tuple[str, ...], scenario: DisturbanceScenario) -> tuple[str, ...]:
    for evidence_id in base:
        require_identifier(evidence_id, "evidence_id")
    return tuple(dict.fromkeys((*base, *scenario.evidence_ids)))


def _all_evidence(
    base: tuple[str, ...], scenarios: tuple[DisturbanceScenario, ...]
) -> tuple[str, ...]:
    merged = base
    for scenario in scenarios:
        merged = _merged_evidence(merged, scenario)
    return merged


@dataclass(frozen=True)
class BarrierVerifier:
    """Require every barrier to hold under every enumerated one-step disturbance."""

    clause_id: str
    transition: AffineTransition
    barriers: tuple[AffineBarrier, ...]
    disturbances: tuple[DisturbanceScenario, ...]
    tolerance: ExactNumber = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        if not self.barriers:
            raise MathConfigurationError("BarrierVerifier requires at least one barrier")
        barrier_ids = [barrier.barrier_id for barrier in self.barriers]
        if len(barrier_ids) != len(set(barrier_ids)):
            raise MathConfigurationError("barrier identifiers must be unique")
        _require_unique_scenarios(self.disturbances)
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise MathConfigurationError("tolerance cannot be negative")
        object.__setattr__(self, "tolerance", tolerance)

    def verify(
        self,
        *,
        state: Mapping[str, ExactNumber],
        control: Mapping[str, ExactNumber],
        evidence_ids: tuple[str, ...] = (),
    ) -> MathResult:
        frozen_state = dict(state)
        frozen_control = dict(control)
        evaluated: list[
            tuple[Decimal, DisturbanceScenario, AffineBarrier, Decimal, dict[str, Decimal]]
        ] = []
        for scenario in self.disturbances:
            next_state = self.transition.evaluate(
                frozen_state, frozen_control, scenario.to_mapping()
            )
            for barrier in self.barriers:
                margin, value = barrier.margin(next_state)
                evaluated.append((margin, scenario, barrier, value, next_state))

        worst = min(evaluated, key=lambda item: (item[0], item[1].scenario_id, item[2].barrier_id))
        margin, scenario, barrier, value, next_state = worst
        tolerance = exact_decimal(self.tolerance, "tolerance")
        with decimal_arithmetic():
            satisfied = margin + tolerance >= 0
        operands = tuple(
            NumericOperand(f"state.{name}", exact_decimal(number, name), barrier.expression.unit)
            for name, number in sorted(frozen_state.items())
        )
        operands += tuple(
            NumericOperand(f"control.{name}", exact_decimal(number, name), barrier.expression.unit)
            for name, number in sorted(frozen_control.items())
        )
        operands += tuple(
            NumericOperand(
                f"disturbance.{item.name}",
                exact_decimal(item.value, item.name),
                barrier.expression.unit,
            )
            for item in scenario.values
        )
        operands += tuple(
            NumericOperand(f"next.{name}", number, barrier.expression.unit)
            for name, number in sorted(next_state.items())
        ) + (
            NumericOperand("barrier_value", value, barrier.expression.unit),
            NumericOperand(
                "barrier_minimum",
                exact_decimal(barrier.minimum, "barrier minimum"),
                barrier.expression.unit,
            ),
        )
        counterexample = None
        if not satisfied:
            with decimal_arithmetic():
                breach = -margin
            counterexample = MathCounterexample(
                self.clause_id,
                f"scenario {scenario.scenario_id!r} breaches barrier {barrier.barrier_id!r} "
                f"by {breach}",
                operands,
            )
        return MathResult(
            clause_id=self.clause_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(ReasonCode.BARRIER_SAFE if satisfied else ReasonCode.BARRIER_BREACHED),
            derivation=(
                f"min over {len(self.disturbances)} declared disturbances and "
                f"{len(self.barriers)} barriers is h={margin}; require h >= -{tolerance}"
            ),
            operands=operands,
            unit=barrier.expression.unit,
            tolerance=tolerance,
            margin=margin,
            assumptions=(
                "one-step model-relative safe-set check",
                "disturbance set is finite and explicitly enumerated",
                "unmodelled dynamics and disturbances are outside this claim",
                *barrier.assumptions,
                *self.assumptions,
            ),
            evidence_ids=_all_evidence(evidence_ids, self.disturbances),
            counterexample=counterexample,
        )


@dataclass(frozen=True)
class QuadraticTerm:
    """A non-negative weighted term ``weight * (x - center)^2``."""

    field: str
    weight: ExactNumber
    center: ExactNumber = Decimal(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", require_identifier(self.field, "potential field"))
        weight = exact_decimal(self.weight, f"weight[{self.field}]")
        if weight < 0:
            raise MathConfigurationError("quadratic potential weights cannot be negative")
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "center", exact_decimal(self.center, f"center[{self.field}]"))


@dataclass(frozen=True)
class QuadraticPotential:
    """A declared non-negative risk potential over selected state coordinates."""

    potential_id: str
    terms: tuple[QuadraticTerm, ...]
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "potential_id", require_identifier(self.potential_id, "potential_id")
        )
        if not self.terms:
            raise MathConfigurationError("quadratic potential requires at least one term")
        fields = [term.field for term in self.terms]
        if len(fields) != len(set(fields)):
            raise MathConfigurationError("quadratic potential fields must be unique")
        object.__setattr__(self, "unit", require_identifier(self.unit, "potential unit"))

    def evaluate(self, state: Mapping[str, ExactNumber]) -> Decimal:
        total = Decimal(0)
        with decimal_arithmetic():
            for term in self.terms:
                if term.field not in state:
                    raise MathInputError(f"state is missing potential field {term.field!r}")
                value = exact_decimal(state[term.field], term.field)
                center = exact_decimal(term.center, f"center[{term.field}]")
                weight = exact_decimal(term.weight, f"weight[{term.field}]")
                total += weight * (value - center) ** 2
        return total


@dataclass(frozen=True)
class RiskStabilityVerifier:
    """Check a finite one-step Lyapunov-style risk budget without universal claims."""

    clause_id: str
    transition: AffineTransition
    potential: QuadraticPotential
    disturbances: tuple[DisturbanceScenario, ...]
    contraction_factor: ExactNumber = Decimal(1)
    additive_budget: ExactNumber = Decimal(0)
    tolerance: ExactNumber = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        _require_unique_scenarios(self.disturbances)
        factor = exact_decimal(self.contraction_factor, "contraction_factor")
        budget = exact_decimal(self.additive_budget, "additive_budget")
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if factor < 0 or factor > 1:
            raise MathConfigurationError("contraction_factor must lie in [0, 1]")
        if budget < 0 or tolerance < 0:
            raise MathConfigurationError("additive_budget and tolerance cannot be negative")
        object.__setattr__(self, "contraction_factor", factor)
        object.__setattr__(self, "additive_budget", budget)
        object.__setattr__(self, "tolerance", tolerance)

    def verify(
        self,
        *,
        state: Mapping[str, ExactNumber],
        control: Mapping[str, ExactNumber],
        evidence_ids: tuple[str, ...] = (),
    ) -> MathResult:
        frozen_state = dict(state)
        frozen_control = dict(control)
        current = self.potential.evaluate(frozen_state)
        evaluated: list[tuple[Decimal, DisturbanceScenario, dict[str, Decimal]]] = []
        for scenario in self.disturbances:
            next_state = self.transition.evaluate(
                frozen_state, frozen_control, scenario.to_mapping()
            )
            evaluated.append((self.potential.evaluate(next_state), scenario, next_state))
        next_value, scenario, next_state = max(
            evaluated, key=lambda item: (item[0], item[1].scenario_id)
        )
        with decimal_arithmetic():
            bound = exact_decimal(self.contraction_factor, "contraction_factor") * current
            bound += exact_decimal(self.additive_budget, "additive_budget")
            margin = bound - next_value
        tolerance = exact_decimal(self.tolerance, "tolerance")
        with decimal_arithmetic():
            satisfied = margin + tolerance >= 0
        operands = tuple(
            NumericOperand(f"state.{name}", exact_decimal(number, name), self.potential.unit)
            for name, number in sorted(frozen_state.items())
        )
        operands += tuple(
            NumericOperand(f"control.{name}", exact_decimal(number, name), self.potential.unit)
            for name, number in sorted(frozen_control.items())
        )
        operands += tuple(
            NumericOperand(
                f"disturbance.{item.name}",
                exact_decimal(item.value, item.name),
                self.potential.unit,
            )
            for item in scenario.values
        )
        operands += tuple(
            NumericOperand(f"next.{name}", number, self.potential.unit)
            for name, number in sorted(next_state.items())
        )
        operands += (
            NumericOperand("potential_current", current, self.potential.unit),
            NumericOperand("potential_next_worst", next_value, self.potential.unit),
            NumericOperand("declared_bound", bound, self.potential.unit),
        )
        counterexample = None
        if not satisfied:
            counterexample = MathCounterexample(
                self.clause_id,
                f"scenario {scenario.scenario_id!r} has next potential {next_value} "
                f"above declared bound {bound}",
                operands,
            )
        return MathResult(
            clause_id=self.clause_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(
                ReasonCode.RISK_BUDGET_SATISFIED if satisfied else ReasonCode.RISK_BUDGET_EXCEEDED
            ),
            derivation=(
                f"max V(next)={next_value}; require V(next) <= "
                f"{self.contraction_factor} * V(now)={current} + {self.additive_budget} = {bound}"
            ),
            operands=operands,
            unit=self.potential.unit,
            tolerance=tolerance,
            margin=margin,
            assumptions=(
                "bounded model-relative Lyapunov-style risk-budget check",
                "not a proof of agent or closed-loop stability",
                "disturbance set is finite and explicitly enumerated",
                *self.assumptions,
            ),
            evidence_ids=_all_evidence(evidence_ids, self.disturbances),
            counterexample=counterexample,
        )
