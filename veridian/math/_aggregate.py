"""Exact rolling-window aggregation for split-action detection."""

from __future__ import annotations

from collections import defaultdict
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


@dataclass(frozen=True)
class AggregateEvent:
    """One authenticated exact amount assigned to a caller-defined aggregate group."""

    event_id: str
    group_id: str
    occurred_at_ms: int
    amount: ExactNumber
    evidence_id: str
    authenticated: bool = True

    def __post_init__(self) -> None:
        for field_name in ("event_id", "group_id", "evidence_id"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )
        if isinstance(self.occurred_at_ms, bool) or not isinstance(self.occurred_at_ms, int):
            raise MathInputError("aggregate event occurred_at_ms must be an integer")
        if self.occurred_at_ms < 0:
            raise MathInputError("aggregate event occurred_at_ms cannot be negative")
        amount = exact_decimal(self.amount, f"amount[{self.event_id}]")
        if amount < 0:
            raise MathInputError("aggregate event amount cannot be negative")
        object.__setattr__(self, "amount", amount)
        if not isinstance(self.authenticated, bool):
            raise MathInputError("aggregate event authenticated flag must be boolean")


@dataclass(frozen=True)
class AggregateVerifier:
    """Bound the sum for each group in a closed authenticated time window."""

    clause_id: str
    window_ms: int
    limit: ExactNumber
    unit: str
    tolerance: ExactNumber = Decimal(0)
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", require_identifier(self.clause_id, "clause_id"))
        if isinstance(self.window_ms, bool) or not isinstance(self.window_ms, int):
            raise MathConfigurationError("window_ms must be an integer")
        if self.window_ms <= 0:
            raise MathConfigurationError("window_ms must be positive")
        limit = exact_decimal(self.limit, "aggregate limit")
        tolerance = exact_decimal(self.tolerance, "tolerance")
        if limit < 0 or tolerance < 0:
            raise MathConfigurationError("aggregate limit and tolerance cannot be negative")
        object.__setattr__(self, "limit", limit)
        object.__setattr__(self, "tolerance", tolerance)
        object.__setattr__(self, "unit", require_identifier(self.unit, "unit"))

    def verify(self, events: tuple[AggregateEvent, ...], *, as_of_ms: int) -> MathResult:
        """Evaluate ``sum(amount[group, t]) <= limit`` over ``[as_of-window, as_of]``."""
        if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int) or as_of_ms < 0:
            raise MathInputError("as_of_ms must be a non-negative integer")
        ordered_events = tuple(
            sorted(
                events,
                key=lambda event: (
                    event.occurred_at_ms,
                    event.event_id,
                    event.group_id,
                    str(event.amount),
                    event.evidence_id,
                    event.authenticated,
                ),
            )
        )
        ids: set[str] = set()
        for event in ordered_events:
            if event.event_id in ids:
                operands = (
                    NumericOperand(
                        f"event[{event.event_id}].amount",
                        exact_decimal(event.amount, "amount"),
                        self.unit,
                    ),
                )
                return MathResult(
                    clause_id=self.clause_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.AGGREGATE_DUPLICATE_EVENT,
                    derivation="aggregate event identifiers must be unique",
                    operands=operands,
                    unit=self.unit,
                    tolerance=exact_decimal(self.tolerance, "tolerance"),
                    margin=None,
                    assumptions=(
                        "duplicate identifiers are rejected before aggregation",
                        *self.assumptions,
                    ),
                    evidence_ids=(event.evidence_id,),
                    counterexample=MathCounterexample(
                        self.clause_id,
                        f"event identifier {event.event_id!r} is duplicated",
                        operands,
                    ),
                )
            ids.add(event.event_id)
            if event.occurred_at_ms > as_of_ms:
                raise MathInputError(f"aggregate event {event.event_id!r} occurs after as_of_ms")

        start_ms = max(0, as_of_ms - self.window_ms)
        in_window = tuple(
            event for event in ordered_events if start_ms <= event.occurred_at_ms <= as_of_ms
        )
        unauthenticated = sorted(
            (event for event in in_window if not event.authenticated),
            key=lambda event: (event.occurred_at_ms, event.event_id),
        )
        if unauthenticated:
            event = unauthenticated[0]
            operands = (
                NumericOperand(
                    f"event[{event.event_id}].amount",
                    exact_decimal(event.amount, "amount"),
                    self.unit,
                ),
            )
            return MathResult(
                clause_id=self.clause_id,
                status=MathStatus.UNKNOWN,
                reason_code=ReasonCode.AGGREGATE_UNAUTHENTICATED,
                derivation=f"in-window event {event.event_id!r} is not authenticated",
                operands=operands,
                unit=self.unit,
                tolerance=exact_decimal(self.tolerance, "tolerance"),
                margin=None,
                assumptions=(
                    "complete authenticated event coverage is required",
                    *self.assumptions,
                ),
                evidence_ids=(event.evidence_id,),
                counterexample=None,
            )

        totals: dict[str, Decimal] = defaultdict(Decimal)
        by_group: dict[str, list[AggregateEvent]] = defaultdict(list)
        with decimal_arithmetic():
            for event in in_window:
                amount = exact_decimal(event.amount, f"amount[{event.event_id}]")
                totals[event.group_id] += amount
                by_group[event.group_id].append(event)

        if totals:
            total = max(totals.values())
            group_id = min(group for group, group_total in totals.items() if group_total == total)
            selected_events = tuple(
                sorted(
                    by_group[group_id],
                    key=lambda event: (event.occurred_at_ms, event.event_id),
                )
            )
        else:
            group_id, total, selected_events = "no-events", Decimal(0), ()

        with decimal_arithmetic():
            margin = exact_decimal(self.limit, "aggregate limit") - total
            margin += exact_decimal(self.tolerance, "tolerance")
        satisfied = margin >= 0
        aggregate_operands = tuple(
            NumericOperand(
                f"event[{event.event_id}].amount",
                exact_decimal(event.amount, "amount"),
                self.unit,
            )
            for event in selected_events
        ) + (
            NumericOperand("window_total", total, self.unit),
            NumericOperand("window_limit", exact_decimal(self.limit, "aggregate limit"), self.unit),
        )
        counterexample = None
        if not satisfied:
            counterexample = MathCounterexample(
                self.clause_id,
                f"group {group_id!r} totals {total}, exceeding limit {self.limit}",
                aggregate_operands,
            )
        return MathResult(
            clause_id=self.clause_id,
            status=MathStatus.SATISFIED if satisfied else MathStatus.VIOLATED,
            reason_code=(
                ReasonCode.AGGREGATE_WITHIN_LIMIT
                if satisfied
                else ReasonCode.AGGREGATE_LIMIT_EXCEEDED
            ),
            derivation=(
                f"max grouped sum in closed window [{start_ms}, {as_of_ms}] is {total}; "
                f"require sum <= {self.limit} with tolerance {self.tolerance}"
            ),
            operands=aggregate_operands,
            unit=self.unit,
            tolerance=exact_decimal(self.tolerance, "tolerance"),
            margin=margin,
            assumptions=(
                "group identifiers and complete window coverage are supplied by trusted policy adapters",
                *self.assumptions,
            ),
            evidence_ids=tuple(event.evidence_id for event in in_window),
            counterexample=counterexample,
        )
