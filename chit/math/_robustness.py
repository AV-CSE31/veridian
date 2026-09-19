"""Finite, explicitly bounded perturbation and metamorphic verification.

These verifiers make claims only about the supplied perturbations. They do not
turn sampled tests into a proof over a continuous neighbourhood.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum

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


class VectorNorm(StrEnum):
    """Supported deterministic norms over homogeneous exact deltas."""

    L1 = "l1"
    L2 = "l2"
    L_INFINITY = "l-infinity"


class MetamorphicRelation(StrEnum):
    """Relations that declared transformations must preserve."""

    INVARIANT = "invariant"
    CONTROL_NON_DECREASING = "control-non-decreasing"


class ControlLevel(IntEnum):
    """Ordered control strength used by monotonic-risk checks."""

    ALLOW = 0
    HOLD = 1
    DENY = 2


@dataclass(frozen=True)
class DeltaComponent:
    """One named component of an exact perturbation vector."""

    name: str
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "delta name"))
        object.__setattr__(self, "value", exact_decimal(self.value, self.name))


def _validate_delta(delta: tuple[DeltaComponent, ...]) -> None:
    if not delta:
        raise MathInputError("a perturbation delta cannot be empty")
    names = [component.name for component in delta]
    if len(names) != len(set(names)):
        raise MathInputError("perturbation component names must be unique")


def _norm(delta: tuple[DeltaComponent, ...], norm: VectorNorm) -> Decimal:
    # A fixed local context makes arithmetic and irrational roots caller-independent.
    with decimal_arithmetic():
        values = tuple(abs(component.value) for component in delta)
        if norm is VectorNorm.L1:
            return sum(values, Decimal(0))
        if norm is VectorNorm.L_INFINITY:
            return max(values)
        squared = sum((value * value for value in values), Decimal(0))
        return squared.sqrt()


def _norm_label(norm: VectorNorm) -> str:
    return {
        VectorNorm.L1: "L1",
        VectorNorm.L2: "L2 (50-digit Decimal)",
        VectorNorm.L_INFINITY: "L-infinity",
    }[norm]


def _validate_evidence_ids(evidence_ids: tuple[str, ...]) -> None:
    for evidence_id in evidence_ids:
        require_identifier(evidence_id, "evidence_id")


@dataclass(frozen=True)
class NumericPerturbation:
    """Observed numeric output for one explicitly declared input delta."""

    case_id: str
    delta: tuple[DeltaComponent, ...]
    observed_output: Decimal
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_identifier(self.case_id, "case_id"))
        _validate_delta(self.delta)
        object.__setattr__(
            self,
            "observed_output",
            exact_decimal(self.observed_output, f"observed_output[{self.case_id}]"),
        )
        _validate_evidence_ids(self.evidence_ids)


@dataclass(frozen=True)
class PerturbationVerifier:
    """Check sampled local numeric robustness inside an explicit norm ball."""

    clause_id: str
    norm: VectorNorm
    radius: Decimal
    output_tolerance: Decimal
    input_unit: str
    output_unit: str
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        if not isinstance(self.norm, VectorNorm):
            raise MathConfigurationError("norm must be a VectorNorm")
        radius = exact_decimal(self.radius, "radius")
        tolerance = exact_decimal(self.output_tolerance, "output_tolerance")
        if radius < 0 or tolerance < 0:
            raise MathConfigurationError("radius and output_tolerance cannot be negative")
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "output_tolerance", tolerance)
        object.__setattr__(self, "input_unit", require_identifier(self.input_unit, "input_unit"))
        object.__setattr__(self, "output_unit", require_identifier(self.output_unit, "output_unit"))

    def verify(
        self, baseline_output: ExactNumber, perturbations: tuple[NumericPerturbation, ...]
    ) -> MathResult:
        """Return the smallest supplied norm witness when output tolerance is exceeded."""
        baseline = exact_decimal(baseline_output, "baseline_output")
        if not perturbations:
            raise MathInputError("at least one perturbation is required")
        if len({sample.case_id for sample in perturbations}) != len(perturbations):
            raise MathInputError("perturbation case identifiers must be unique")

        evaluated: list[tuple[Decimal, NumericPerturbation, Decimal]] = []
        for sample in perturbations:
            distance = _norm(sample.delta, self.norm)
            if distance > self.radius:
                raise MathInputError(
                    f"perturbation {sample.case_id!r} has norm {distance} outside radius {self.radius}"
                )
            with decimal_arithmetic():
                margin = self.output_tolerance - abs(sample.observed_output - baseline)
            evaluated.append((distance, sample, margin))

        violations = [item for item in evaluated if item[2] < 0]
        witness = (
            min(violations, key=lambda item: (item[0], item[1].case_id)) if violations else None
        )
        reported_margin = witness[2] if witness is not None else min(item[2] for item in evaluated)
        evidence_ids = tuple(
            sorted({item for _, sample, _ in evaluated for item in sample.evidence_ids})
        )
        representative = (
            witness
            if witness is not None
            else min(evaluated, key=lambda item: (item[2], item[0], item[1].case_id))
        )
        distance, sample, _ = representative
        operands: tuple[NumericOperand, ...] = (
            NumericOperand("baseline_output", baseline, self.output_unit),
        )
        operands += tuple(
            NumericOperand(component.name, component.value, self.input_unit)
            for component in sample.delta
        )
        operands += (
            NumericOperand("observed_output", sample.observed_output, self.output_unit),
            NumericOperand("perturbation_norm", distance, self.input_unit),
        )
        counterexample = None
        if witness is not None:
            distance, sample, _ = witness
            counterexample = MathCounterexample(
                self.clause_id,
                f"case {sample.case_id!r} changes output by "
                f"{abs(sample.observed_output - baseline)} at norm {distance}",
                operands,
            )

        satisfied = witness is None
        return MathResult(
            clause_id=self.clause_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(
                ReasonCode.PERTURBATION_ROBUST if satisfied else ReasonCode.PERTURBATION_VIOLATED
            ),
            derivation=(
                f"{len(evaluated)} supplied perturbations have {_norm_label(self.norm)} norm "
                f"<= {self.radius}; require abs(output - baseline) <= {self.output_tolerance}"
            ),
            operands=operands,
            unit=self.output_unit,
            tolerance=self.output_tolerance,
            margin=reported_margin,
            assumptions=(
                "finite supplied perturbation set",
                *self.assumptions,
            ),
            evidence_ids=evidence_ids,
            counterexample=counterexample,
        )


@dataclass(frozen=True)
class ControlPerturbation:
    """Observed control level after one named metamorphic transformation."""

    case_id: str
    transformation_id: str
    delta: tuple[DeltaComponent, ...]
    observed: ControlLevel
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_identifier(self.case_id, "case_id"))
        object.__setattr__(
            self,
            "transformation_id",
            require_identifier(self.transformation_id, "transformation_id"),
        )
        _validate_delta(self.delta)
        if not isinstance(self.observed, ControlLevel):
            raise MathInputError("observed must be a ControlLevel")
        _validate_evidence_ids(self.evidence_ids)


@dataclass(frozen=True)
class MetamorphicVerifier:
    """Verify decision invariance or monotonic control on supplied transformations."""

    clause_id: str
    relation: MetamorphicRelation
    norm: VectorNorm
    radius: Decimal
    input_unit: str
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        if not isinstance(self.relation, MetamorphicRelation):
            raise MathConfigurationError("relation must be a MetamorphicRelation")
        if not isinstance(self.norm, VectorNorm):
            raise MathConfigurationError("norm must be a VectorNorm")
        radius = exact_decimal(self.radius, "radius")
        if radius < 0:
            raise MathConfigurationError("radius cannot be negative")
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "input_unit", require_identifier(self.input_unit, "input_unit"))

    def verify(
        self, baseline: ControlLevel, perturbations: tuple[ControlPerturbation, ...]
    ) -> MathResult:
        if not isinstance(baseline, ControlLevel):
            raise MathInputError("baseline must be a ControlLevel")
        if not perturbations:
            raise MathInputError("at least one control perturbation is required")
        if len({sample.case_id for sample in perturbations}) != len(perturbations):
            raise MathInputError("control perturbation case identifiers must be unique")

        evaluated: list[tuple[Decimal, ControlPerturbation, Decimal]] = []
        for sample in perturbations:
            distance = _norm(sample.delta, self.norm)
            if distance > self.radius:
                raise MathInputError(
                    f"perturbation {sample.case_id!r} has norm {distance} outside radius {self.radius}"
                )
            if self.relation is MetamorphicRelation.INVARIANT:
                margin = -Decimal(abs(int(sample.observed) - int(baseline)))
            else:
                margin = Decimal(int(sample.observed) - int(baseline))
            evaluated.append((distance, sample, margin))

        violations = [item for item in evaluated if item[2] < 0]
        witness = (
            min(violations, key=lambda item: (item[0], item[1].case_id)) if violations else None
        )
        worst_margin = min(item[2] for item in evaluated)
        evidence_ids = tuple(
            sorted({item for _, sample, _ in evaluated for item in sample.evidence_ids})
        )
        representative = (
            witness
            if witness is not None
            else min(evaluated, key=lambda item: (item[2], item[0], item[1].case_id))
        )
        distance, sample, _ = representative
        operands: tuple[NumericOperand, ...] = (
            NumericOperand("baseline_control", Decimal(int(baseline)), "control-level"),
        )
        operands += tuple(
            NumericOperand(component.name, component.value, self.input_unit)
            for component in sample.delta
        )
        operands += (
            NumericOperand("observed_control", Decimal(int(sample.observed)), "control-level"),
            NumericOperand("perturbation_norm", distance, self.input_unit),
        )
        counterexample = None
        if witness is not None:
            distance, sample, _ = witness
            counterexample = MathCounterexample(
                self.clause_id,
                f"case {sample.case_id!r} ({sample.transformation_id}) changes control "
                f"{baseline.name} -> {sample.observed.name}",
                operands,
            )

        relation_text = (
            "decision invariance"
            if self.relation is MetamorphicRelation.INVARIANT
            else "monotone non-decreasing control"
        )
        satisfied = witness is None
        return MathResult(
            clause_id=self.clause_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(
                ReasonCode.METAMORPHIC_SATISFIED if satisfied else ReasonCode.METAMORPHIC_VIOLATED
            ),
            derivation=(
                f"{len(evaluated)} supplied transformations within {_norm_label(self.norm)} "
                f"radius {self.radius} require {relation_text}"
            ),
            operands=operands,
            unit="control-level",
            tolerance=Decimal(0),
            margin=worst_margin,
            assumptions=(
                "finite supplied transformation set",
                "transformation semantics and control ordering are declared by policy",
                *self.assumptions,
            ),
            evidence_ids=evidence_ids,
            counterexample=counterexample,
        )
