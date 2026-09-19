"""Exact linear equality, conservation, and bound invariant verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

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
    decimal_arithmetic,
    exact_decimal,
    require_identifier,
)


@dataclass(frozen=True)
class LinearTerm:
    """A named coefficient in an exact affine expression."""

    field: str
    coefficient: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", require_identifier(self.field, "term field"))
        object.__setattr__(
            self, "coefficient", exact_decimal(self.coefficient, f"coefficient[{self.field}]")
        )


@dataclass(frozen=True)
class LinearExpression:
    """Sparse exact affine expression evaluated over a named numeric snapshot."""

    terms: tuple[LinearTerm, ...]
    constant: Decimal = Decimal(0)
    unit: str = "dimensionless"

    def __post_init__(self) -> None:
        fields = [term.field for term in self.terms]
        if len(fields) != len(set(fields)):
            raise MathConfigurationError("linear expression fields must be unique")
        object.__setattr__(self, "constant", exact_decimal(self.constant, "constant"))
        object.__setattr__(self, "unit", require_identifier(self.unit, "expression unit"))

    @classmethod
    def field(cls, field: str, *, unit: str = "dimensionless") -> LinearExpression:
        return cls((LinearTerm(field),), unit=unit)

    def evaluate(
        self, snapshot: Mapping[str, ExactNumber]
    ) -> tuple[Decimal, tuple[NumericOperand, ...]]:
        total = exact_decimal(self.constant, "constant")
        operands: list[NumericOperand] = []
        with decimal_arithmetic():
            for term in self.terms:
                if term.field not in snapshot:
                    raise _MissingField(term.field)
                value = exact_decimal(snapshot[term.field], term.field)
                operands.append(NumericOperand(term.field, value, self.unit))
                total += term.coefficient * value
        return total, tuple(operands)


class _MissingField(MathInputError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"snapshot is missing {field!r}")


class InvariantClause(Protocol):
    invariant_id: str

    def evaluate(
        self, snapshot: Mapping[str, ExactNumber], evidence_ids: tuple[str, ...]
    ) -> MathResult:
        """Evaluate one clause over an exact snapshot."""


@dataclass(frozen=True)
class EqualityInvariant:
    """Require two affine quantities with the same unit to be equal within tolerance."""

    invariant_id: str
    left: LinearExpression
    right: LinearExpression
    tolerance: Decimal = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invariant_id", require_identifier(self.invariant_id, "invariant_id")
        )
        if self.left.unit != self.right.unit:
            raise MathConfigurationError("equality expressions must use the same unit")
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise MathConfigurationError("tolerance cannot be negative")
        object.__setattr__(self, "tolerance", tolerance)

    def evaluate(
        self, snapshot: Mapping[str, ExactNumber], evidence_ids: tuple[str, ...]
    ) -> MathResult:
        left, left_operands = self.left.evaluate(snapshot)
        right, right_operands = self.right.evaluate(snapshot)
        with decimal_arithmetic():
            residual = abs(left - right)
            margin = self.tolerance - residual
        satisfied = margin >= 0
        operands = left_operands + right_operands
        return MathResult(
            clause_id=self.invariant_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(
                ReasonCode.EQUALITY_SATISFIED if satisfied else ReasonCode.EQUALITY_VIOLATED
            ),
            derivation=f"abs(left - right) = {residual} <= tolerance {self.tolerance}",
            operands=operands,
            unit=self.left.unit,
            tolerance=self.tolerance,
            margin=margin,
            assumptions=self.assumptions,
            evidence_ids=evidence_ids,
            counterexample=(
                None
                if satisfied
                else MathCounterexample(
                    self.invariant_id,
                    f"equality residual {residual} exceeds tolerance {self.tolerance}",
                    operands,
                )
            ),
        )


@dataclass(frozen=True)
class ConservationInvariant:
    """Require the exact sum of named inflows to equal the exact sum of outflows."""

    invariant_id: str
    inflows: tuple[str, ...]
    outflows: tuple[str, ...]
    unit: str
    tolerance: Decimal = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invariant_id", require_identifier(self.invariant_id, "invariant_id")
        )
        if not self.inflows or not self.outflows:
            raise MathConfigurationError("conservation requires inflows and outflows")
        if len(set(self.inflows + self.outflows)) != len(self.inflows + self.outflows):
            raise MathConfigurationError("conservation fields must be unique")
        for field in self.inflows + self.outflows:
            require_identifier(field, "conservation field")
        object.__setattr__(self, "unit", require_identifier(self.unit, "unit"))
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise MathConfigurationError("tolerance cannot be negative")
        object.__setattr__(self, "tolerance", tolerance)

    def evaluate(
        self, snapshot: Mapping[str, ExactNumber], evidence_ids: tuple[str, ...]
    ) -> MathResult:
        operands: list[NumericOperand] = []
        inflow = Decimal(0)
        outflow = Decimal(0)
        with decimal_arithmetic():
            for field in self.inflows:
                if field not in snapshot:
                    raise _MissingField(field)
                value = exact_decimal(snapshot[field], field)
                inflow += value
                operands.append(NumericOperand(field, value, self.unit))
            for field in self.outflows:
                if field not in snapshot:
                    raise _MissingField(field)
                value = exact_decimal(snapshot[field], field)
                outflow += value
                operands.append(NumericOperand(field, value, self.unit))
            residual = abs(inflow - outflow)
            margin = self.tolerance - residual
        satisfied = margin >= 0
        reason = (
            ReasonCode.CONSERVATION_SATISFIED if satisfied else ReasonCode.CONSERVATION_VIOLATED
        )
        return MathResult(
            clause_id=self.invariant_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=reason,
            derivation=(
                f"abs(sum(inflows) {inflow} - sum(outflows) {outflow}) = {residual} "
                f"<= tolerance {self.tolerance}"
            ),
            operands=tuple(operands),
            unit=self.unit,
            tolerance=self.tolerance,
            margin=margin,
            assumptions=self.assumptions,
            evidence_ids=evidence_ids,
            counterexample=(
                None
                if satisfied
                else MathCounterexample(
                    self.invariant_id,
                    f"conservation residual {residual} exceeds tolerance {self.tolerance}",
                    tuple(operands),
                )
            ),
        )


@dataclass(frozen=True)
class BoundInvariant:
    """Require an affine quantity to remain inside an optional closed interval."""

    invariant_id: str
    expression: LinearExpression
    lower: Decimal | int | None = None
    upper: Decimal | int | None = None
    tolerance: Decimal = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "invariant_id", require_identifier(self.invariant_id, "invariant_id")
        )
        if self.lower is None and self.upper is None:
            raise MathConfigurationError("a bound requires lower, upper, or both")
        lower = exact_decimal(self.lower, "lower") if self.lower is not None else None
        upper = exact_decimal(self.upper, "upper") if self.upper is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise MathConfigurationError("lower bound cannot exceed upper bound")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise MathConfigurationError("tolerance cannot be negative")
        object.__setattr__(self, "tolerance", tolerance)

    def evaluate(
        self, snapshot: Mapping[str, ExactNumber], evidence_ids: tuple[str, ...]
    ) -> MathResult:
        value, operands = self.expression.evaluate(snapshot)
        margins: list[Decimal] = []
        with decimal_arithmetic():
            if self.lower is not None:
                margins.append(value - self.lower + self.tolerance)
            if self.upper is not None:
                margins.append(self.upper - value + self.tolerance)
        margin = min(margins)
        satisfied = margin >= 0
        interval = f"[{self.lower if self.lower is not None else '-inf'}, "
        interval += f"{self.upper if self.upper is not None else '+inf'}]"
        return MathResult(
            clause_id=self.invariant_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(ReasonCode.BOUNDS_SATISFIED if satisfied else ReasonCode.BOUNDS_VIOLATED),
            derivation=(
                f"value {value} in closed interval {interval} with tolerance {self.tolerance}"
            ),
            operands=operands,
            unit=self.expression.unit,
            tolerance=self.tolerance,
            margin=margin,
            assumptions=self.assumptions,
            evidence_ids=evidence_ids,
            counterexample=(
                None
                if satisfied
                else MathCounterexample(
                    self.invariant_id,
                    f"value {value} lies outside {interval}",
                    operands,
                )
            ),
        )


@dataclass(frozen=True)
class InvariantVerifier:
    """Evaluate a fixed ordered set of stateless exact invariant clauses."""

    invariants: tuple[InvariantClause, ...]

    def __post_init__(self) -> None:
        if not self.invariants:
            raise MathConfigurationError("InvariantVerifier requires at least one invariant")
        ids = [invariant.invariant_id for invariant in self.invariants]
        if len(ids) != len(set(ids)):
            raise MathConfigurationError("invariant identifiers must be unique")

    def verify(
        self,
        snapshot: Mapping[str, ExactNumber],
        *,
        evidence_ids: tuple[str, ...] = (),
    ) -> MathReport:
        """Evaluate all clauses; missing evidence is UNKNOWN rather than a false pass."""
        frozen_snapshot = dict(snapshot)
        results: list[MathResult] = []
        for invariant in self.invariants:
            try:
                results.append(invariant.evaluate(frozen_snapshot, evidence_ids))
            except _MissingField as exc:
                results.append(
                    MathResult(
                        clause_id=invariant.invariant_id,
                        status=MathStatus.UNKNOWN,
                        reason_code=ReasonCode.INPUT_MISSING,
                        derivation=f"required snapshot field {exc.field!r} is absent",
                        operands=(),
                        unit="unknown",
                        tolerance=Decimal(0),
                        margin=None,
                        assumptions=(),
                        evidence_ids=evidence_ids,
                        counterexample=None,
                    )
                )
        return MathReport(tuple(results))
