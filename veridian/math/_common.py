"""Shared immutable result and exact-number types for mathematical verifiers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from enum import StrEnum

from veridian.core.exceptions import VeridianError

ExactNumber = int | Decimal

DECIMAL_PRECISION = 50
DECIMAL_ASSUMPTION = "Decimal arithmetic uses precision=50 and ROUND_HALF_EVEN"
_DECIMAL_CONTEXT = Context(
    prec=DECIMAL_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999_999,
    Emax=999_999_999,
)


@contextmanager
def decimal_arithmetic() -> Iterator[None]:
    """Use Veridian's fixed 50-digit, half-even arithmetic context."""
    try:
        with localcontext(_DECIMAL_CONTEXT):
            yield
    except DecimalException as exc:
        raise MathInputError(f"decimal arithmetic failed: {type(exc).__name__}") from exc


class MathVerificationError(VeridianError):
    """Base error for the deterministic mathematics package."""


class MathConfigurationError(MathVerificationError):
    """A mathematical verifier or model is not well-defined."""


class MathInputError(MathVerificationError):
    """An evaluated snapshot is malformed or contains inexact values."""


class MathStatus(StrEnum):
    """Four-valued verification outcome compatible with assurance clauses."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    ERROR = "error"


class ReasonCode(StrEnum):
    """Stable machine-readable reasons emitted by mathematical verifiers."""

    EQUALITY_SATISFIED = "math.equality.satisfied"
    EQUALITY_VIOLATED = "math.equality.violated"
    CONSERVATION_SATISFIED = "math.conservation.satisfied"
    CONSERVATION_VIOLATED = "math.conservation.violated"
    BOUNDS_SATISFIED = "math.bounds.satisfied"
    BOUNDS_VIOLATED = "math.bounds.violated"
    INPUT_MISSING = "math.input.missing"
    PERTURBATION_ROBUST = "math.perturbation.robust"
    PERTURBATION_VIOLATED = "math.perturbation.violated"
    METAMORPHIC_SATISFIED = "math.metamorphic.satisfied"
    METAMORPHIC_VIOLATED = "math.metamorphic.violated"
    BARRIER_SAFE = "math.barrier.safe"
    BARRIER_BREACHED = "math.barrier.breached"
    RISK_BUDGET_SATISFIED = "math.risk_budget.satisfied"
    RISK_BUDGET_EXCEEDED = "math.risk_budget.exceeded"
    TRAJECTORY_RULE_SATISFIED = "math.trajectory.rule_satisfied"
    TRAJECTORY_RULE_VIOLATED = "math.trajectory.rule_violated"
    TRAJECTORY_INCOMPLETE = "math.trajectory.incomplete"
    TRAJECTORY_UNAUTHENTICATED = "math.trajectory.unauthenticated"
    TRAJECTORY_ORDER_INVALID = "math.trajectory.order_invalid"
    TRAJECTORY_DUPLICATE_EVENT = "math.trajectory.duplicate_event"
    STATE_TRANSITION_INVALID = "math.state_machine.transition_invalid"
    STATE_NOT_ACCEPTING = "math.state_machine.not_accepting"
    AGGREGATE_WITHIN_LIMIT = "math.aggregate.within_limit"
    AGGREGATE_LIMIT_EXCEEDED = "math.aggregate.limit_exceeded"
    AGGREGATE_UNAUTHENTICATED = "math.aggregate.unauthenticated"
    AGGREGATE_DUPLICATE_EVENT = "math.aggregate.duplicate_event"


def exact_decimal(value: ExactNumber, field_name: str) -> Decimal:
    """Convert an integer or finite Decimal without accepting binary floats."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise MathInputError(f"{field_name} must be an integer or Decimal")
    converted = Decimal(value)
    if not converted.is_finite():
        raise MathInputError(f"{field_name} must be finite")
    return converted


def require_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MathConfigurationError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class NumericOperand:
    """One named exact numeric operand disclosed in a derivation."""

    name: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "operand name"))
        object.__setattr__(self, "value", exact_decimal(self.value, self.name))
        object.__setattr__(self, "unit", require_identifier(self.unit, "operand unit"))


@dataclass(frozen=True)
class MathCounterexample:
    """A concrete witness showing why a mathematical clause did not hold."""

    clause_id: str
    summary: str
    operands: tuple[NumericOperand, ...] = ()
    event_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        object.__setattr__(self, "summary", require_identifier(self.summary, "summary"))
        if self.event_index is not None and self.event_index < 0:
            raise MathConfigurationError("event_index cannot be negative")


@dataclass(frozen=True)
class MathResult:
    """Deterministic, counterexample-bearing result for one mathematical clause."""

    clause_id: str
    status: MathStatus
    reason_code: ReasonCode
    derivation: str
    operands: tuple[NumericOperand, ...]
    unit: str
    tolerance: Decimal
    margin: Decimal | None
    assumptions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    counterexample: MathCounterexample | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        if not isinstance(self.status, MathStatus):
            raise MathConfigurationError("status must be a MathStatus")
        if not isinstance(self.reason_code, ReasonCode):
            raise MathConfigurationError("reason_code must be a ReasonCode")
        object.__setattr__(self, "derivation", require_identifier(self.derivation, "derivation"))
        object.__setattr__(self, "unit", require_identifier(self.unit, "unit"))
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise MathConfigurationError("tolerance cannot be negative")
        object.__setattr__(self, "tolerance", tolerance)
        if self.margin is not None:
            object.__setattr__(self, "margin", exact_decimal(self.margin, "margin"))
        assumptions = tuple(self.assumptions)
        for assumption in assumptions:
            require_identifier(assumption, "assumption")
        if DECIMAL_ASSUMPTION not in assumptions:
            assumptions += (DECIMAL_ASSUMPTION,)
        object.__setattr__(self, "assumptions", assumptions)
        for evidence_id in self.evidence_ids:
            require_identifier(evidence_id, "evidence_id")


@dataclass(frozen=True)
class MathReport:
    """A deterministic aggregation that preserves every individual clause result."""

    results: tuple[MathResult, ...]

    def __post_init__(self) -> None:
        if not self.results:
            raise MathConfigurationError("a mathematical report must contain a result")

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
