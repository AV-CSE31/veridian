"""Authenticated state-machine and bounded temporal trajectory monitors."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ._common import (
    MathConfigurationError,
    MathCounterexample,
    MathInputError,
    MathReport,
    MathResult,
    MathStatus,
    NumericOperand,
    ReasonCode,
    require_identifier,
)


@dataclass(frozen=True)
class TrajectoryEvent:
    """One ordered event bound to authenticated evidence for a single subject."""

    event_id: str
    subject_id: str
    event_type: str
    sequence: int
    occurred_at_ms: int
    evidence_id: str
    authenticated: bool = True

    def __post_init__(self) -> None:
        for field_name in ("event_id", "subject_id", "event_type", "evidence_id"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise MathInputError("event sequence must be an integer")
        if isinstance(self.occurred_at_ms, bool) or not isinstance(self.occurred_at_ms, int):
            raise MathInputError("event occurred_at_ms must be an integer")
        if self.sequence < 0 or self.occurred_at_ms < 0:
            raise MathInputError("event sequence and occurred_at_ms cannot be negative")
        if not isinstance(self.authenticated, bool):
            raise MathInputError("event authenticated flag must be boolean")


class TrajectoryRule(Protocol):
    """Public seam for deterministic bounded temporal monitors."""

    rule_id: str

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        """Evaluate one rule over validated ordered authenticated events."""


def _evidence(base: tuple[str, ...], events: tuple[TrajectoryEvent, ...]) -> tuple[str, ...]:
    for evidence_id in base:
        require_identifier(evidence_id, "evidence_id")
    return tuple(dict.fromkeys((*base, *(event.evidence_id for event in events))))


def _counterexample(
    rule_id: str,
    summary: str,
    event: TrajectoryEvent,
    index: int,
    *,
    extra_operands: tuple[NumericOperand, ...] = (),
) -> MathCounterexample:
    operands = (
        NumericOperand("event_sequence", Decimal(event.sequence), "sequence"),
        NumericOperand("event_time", Decimal(event.occurred_at_ms), "millisecond"),
        *extra_operands,
    )
    return MathCounterexample(rule_id, summary, operands, event_index=index)


def _result(
    *,
    rule_id: str,
    status: MathStatus,
    reason_code: ReasonCode,
    derivation: str,
    events: tuple[TrajectoryEvent, ...],
    evidence_ids: tuple[str, ...],
    margin: Decimal | int | None,
    tolerance: Decimal | int = 0,
    unit: str = "event",
    assumptions: tuple[str, ...] = (),
    counterexample: MathCounterexample | None = None,
) -> MathResult:
    return MathResult(
        clause_id=rule_id,
        status=status,
        reason_code=reason_code,
        derivation=derivation,
        operands=(NumericOperand("event_count", Decimal(len(events)), "event"),),
        unit=unit,
        tolerance=Decimal(tolerance),
        margin=Decimal(margin) if margin is not None else None,
        assumptions=(
            "events are evaluated only within the supplied bounded trajectory",
            *assumptions,
        ),
        evidence_ids=_evidence(evidence_ids, events),
        counterexample=counterexample,
    )


@dataclass(frozen=True)
class StateTransition:
    """One deterministic labelled transition in a finite state machine."""

    from_state: str
    event_type: str
    to_state: str

    def __post_init__(self) -> None:
        for field_name in ("from_state", "event_type", "to_state"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )


@dataclass(frozen=True)
class StateMachineMonitor:
    """Apply the same deterministic finite-state machine independently per subject."""

    rule_id: str
    initial_state: str
    transitions: tuple[StateTransition, ...]
    accepting_states: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", require_identifier(self.rule_id, "rule_id"))
        object.__setattr__(
            self, "initial_state", require_identifier(self.initial_state, "initial_state")
        )
        if not self.transitions:
            raise MathConfigurationError("state machine requires at least one transition")
        keys = [(item.from_state, item.event_type) for item in self.transitions]
        if len(keys) != len(set(keys)):
            raise MathConfigurationError("state machine transitions must be deterministic")
        if not self.accepting_states:
            raise MathConfigurationError("state machine requires an accepting state")
        for state in self.accepting_states:
            require_identifier(state, "accepting state")

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        table = {
            (transition.from_state, transition.event_type): transition.to_state
            for transition in self.transitions
        }
        states: dict[str, str] = defaultdict(lambda: self.initial_state)
        last_index: dict[str, int] = {}
        for index, event in enumerate(events):
            state = states[event.subject_id]
            next_state = table.get((state, event.event_type))
            if next_state is None:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} ({event.event_type}) is not enabled in state {state!r}",
                    event,
                    index,
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.STATE_TRANSITION_INVALID,
                    derivation="every observed event must label an enabled state transition",
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=-1,
                    counterexample=witness,
                )
            states[event.subject_id] = next_state
            last_index[event.subject_id] = index

        not_accepting = sorted(
            subject for subject, state in states.items() if state not in self.accepting_states
        )
        if not_accepting:
            subject = not_accepting[0]
            index = last_index[subject]
            event = events[index]
            if not complete:
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.UNKNOWN,
                    reason_code=ReasonCode.TRAJECTORY_INCOMPLETE,
                    derivation=f"subject {subject!r} has not yet reached an accepting state",
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=None,
                    assumptions=(
                        "bounded observation",
                        "trajectory is explicitly marked incomplete",
                    ),
                )
            witness = _counterexample(
                self.rule_id,
                f"subject {subject!r} ended in non-accepting state {states[subject]!r}",
                event,
                index,
            )
            return _result(
                rule_id=self.rule_id,
                status=MathStatus.VIOLATED,
                reason_code=ReasonCode.STATE_NOT_ACCEPTING,
                derivation="every complete subject trajectory must end in an accepting state",
                events=events,
                evidence_ids=evidence_ids,
                margin=-1,
                counterexample=witness,
            )
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation="all events label enabled transitions and all subjects are accepting",
            events=events,
            evidence_ids=evidence_ids,
            margin=0,
        )


@dataclass(frozen=True)
class PrecedenceRule:
    """Require a prerequisite event earlier than every dependent event per subject."""

    rule_id: str
    prerequisite_event: str
    dependent_event: str

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "prerequisite_event", "dependent_event"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        del complete
        observed: set[str] = set()
        for index, event in enumerate(events):
            if event.event_type == self.dependent_event and event.subject_id not in observed:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} has no preceding {self.prerequisite_event!r}",
                    event,
                    index,
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=(
                        f"{self.prerequisite_event!r} must precede every "
                        f"{self.dependent_event!r} for the same subject"
                    ),
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=-1,
                    counterexample=witness,
                )
            if event.event_type == self.prerequisite_event:
                observed.add(event.subject_id)
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation=(
                f"{self.prerequisite_event!r} precedes every {self.dependent_event!r} "
                "for the same subject"
            ),
            events=events,
            evidence_ids=evidence_ids,
            margin=0,
        )


@dataclass(frozen=True)
class AtMostOnceRule:
    """Require at most one occurrence of an event type per subject."""

    rule_id: str
    event_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", require_identifier(self.rule_id, "rule_id"))
        object.__setattr__(self, "event_type", require_identifier(self.event_type, "event_type"))

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        del complete
        counts: dict[str, int] = defaultdict(int)
        for index, event in enumerate(events):
            if event.event_type != self.event_type:
                continue
            counts[event.subject_id] += 1
            if counts[event.subject_id] > 1:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} repeats single-use event {self.event_type!r}",
                    event,
                    index,
                    extra_operands=(
                        NumericOperand(
                            "occurrence_count", Decimal(counts[event.subject_id]), "event"
                        ),
                    ),
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=f"count({self.event_type!r}) per subject <= 1",
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=1 - counts[event.subject_id],
                    counterexample=witness,
                )
        maximum = max(counts.values(), default=0)
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation=f"count({self.event_type!r}) per subject <= 1",
            events=events,
            evidence_ids=evidence_ids,
            margin=1 - maximum,
        )


@dataclass(frozen=True)
class FreshnessRule:
    """Require fresh preceding evidence when a consuming event occurs."""

    rule_id: str
    evidence_event: str
    consuming_event: str
    max_age_ms: int

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "evidence_event", "consuming_event"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )
        if isinstance(self.max_age_ms, bool) or not isinstance(self.max_age_ms, int):
            raise MathConfigurationError("max_age_ms must be an integer")
        if self.max_age_ms < 0:
            raise MathConfigurationError("max_age_ms cannot be negative")

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        del complete
        latest: dict[str, TrajectoryEvent] = {}
        worst_margin = Decimal(self.max_age_ms)
        for index, event in enumerate(events):
            if event.event_type == self.evidence_event:
                latest[event.subject_id] = event
            if event.event_type != self.consuming_event:
                continue
            source = latest.get(event.subject_id)
            if source is None:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} has no preceding {self.evidence_event!r}",
                    event,
                    index,
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=(
                        f"latest {self.evidence_event!r} age at {self.consuming_event!r} "
                        f"must be <= {self.max_age_ms} ms"
                    ),
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=None,
                    unit="millisecond",
                    counterexample=witness,
                )
            age = event.occurred_at_ms - source.occurred_at_ms
            margin = Decimal(self.max_age_ms - age)
            worst_margin = min(worst_margin, margin)
            if margin < 0:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} uses {self.evidence_event!r} evidence aged {age} ms",
                    event,
                    index,
                    extra_operands=(NumericOperand("evidence_age", Decimal(age), "millisecond"),),
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=(
                        f"latest {self.evidence_event!r} age at {self.consuming_event!r} "
                        f"must be <= {self.max_age_ms} ms"
                    ),
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=margin,
                    unit="millisecond",
                    counterexample=witness,
                )
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation=(
                f"latest {self.evidence_event!r} age at {self.consuming_event!r} "
                f"is <= {self.max_age_ms} ms"
            ),
            events=events,
            evidence_ids=evidence_ids,
            margin=worst_margin,
            unit="millisecond",
        )


@dataclass(frozen=True)
class ForbiddenAfterRule:
    """Forbid one event after a trigger for the same subject."""

    rule_id: str
    trigger_event: str
    forbidden_event: str

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "trigger_event", "forbidden_event"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        del complete
        triggered: set[str] = set()
        for index, event in enumerate(events):
            if event.event_type == self.forbidden_event and event.subject_id in triggered:
                witness = _counterexample(
                    self.rule_id,
                    f"event {event.event_id!r} occurs after {self.trigger_event!r}",
                    event,
                    index,
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=(
                        f"{self.forbidden_event!r} must never follow {self.trigger_event!r} "
                        "for the same subject"
                    ),
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=-1,
                    counterexample=witness,
                )
            if event.event_type == self.trigger_event:
                triggered.add(event.subject_id)
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation=(
                f"{self.forbidden_event!r} never follows {self.trigger_event!r} "
                "for the same subject"
            ),
            events=events,
            evidence_ids=evidence_ids,
            margin=0,
        )


@dataclass(frozen=True)
class TerminalOutcomeRule:
    """Require a terminal event within a declared bounded observation window."""

    rule_id: str
    start_event: str
    terminal_events: frozenset[str]
    max_delay_ms: int

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "start_event"):
            object.__setattr__(
                self, field_name, require_identifier(getattr(self, field_name), field_name)
            )
        if not self.terminal_events:
            raise MathConfigurationError("terminal outcome rule requires a terminal event")
        for event_type in self.terminal_events:
            require_identifier(event_type, "terminal event")
        if isinstance(self.max_delay_ms, bool) or not isinstance(self.max_delay_ms, int):
            raise MathConfigurationError("max_delay_ms must be an integer")
        if self.max_delay_ms < 0:
            raise MathConfigurationError("max_delay_ms cannot be negative")

    def evaluate(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...],
    ) -> MathResult:
        starts = [
            (index, event)
            for index, event in enumerate(events)
            if event.event_type == self.start_event
        ]
        worst_margin = Decimal(self.max_delay_ms)
        for start_index, start in starts:
            terminal = next(
                (
                    (index, event)
                    for index, event in enumerate(events[start_index + 1 :], start_index + 1)
                    if event.subject_id == start.subject_id
                    and event.event_type in self.terminal_events
                ),
                None,
            )
            if terminal is not None:
                terminal_index, terminal_event = terminal
                age = terminal_event.occurred_at_ms - start.occurred_at_ms
                margin = Decimal(self.max_delay_ms - age)
                worst_margin = min(worst_margin, margin)
                if margin >= 0:
                    continue
                witness = _counterexample(
                    self.rule_id,
                    f"terminal event {terminal_event.event_id!r} arrived after {age} ms",
                    terminal_event,
                    terminal_index,
                    extra_operands=(NumericOperand("terminal_delay", Decimal(age), "millisecond"),),
                )
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.VIOLATED,
                    reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                    derivation=f"a terminal outcome must follow within {self.max_delay_ms} ms",
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=margin,
                    unit="millisecond",
                    assumptions=("bounded observation window is closed by elapsed time",),
                    counterexample=witness,
                )

            last_time = max(
                (event.occurred_at_ms for event in events if event.subject_id == start.subject_id),
                default=start.occurred_at_ms,
            )
            elapsed = last_time - start.occurred_at_ms
            window_expired = elapsed > self.max_delay_ms
            if not complete and not window_expired:
                return _result(
                    rule_id=self.rule_id,
                    status=MathStatus.UNKNOWN,
                    reason_code=ReasonCode.TRAJECTORY_INCOMPLETE,
                    derivation="terminal outcome cannot yet be decided from an open trajectory",
                    events=events,
                    evidence_ids=evidence_ids,
                    margin=None,
                    unit="millisecond",
                    assumptions=(
                        "bounded observation",
                        "trajectory is explicitly marked incomplete",
                    ),
                )
            witness = _counterexample(
                self.rule_id,
                f"start event {start.event_id!r} has no terminal outcome",
                start,
                start_index,
            )
            return _result(
                rule_id=self.rule_id,
                status=MathStatus.VIOLATED,
                reason_code=ReasonCode.TRAJECTORY_RULE_VIOLATED,
                derivation=f"a terminal outcome must follow within {self.max_delay_ms} ms",
                events=events,
                evidence_ids=evidence_ids,
                margin=Decimal(self.max_delay_ms - elapsed) if window_expired else None,
                unit="millisecond",
                assumptions=("bounded observation is closed",),
                counterexample=witness,
            )
        return _result(
            rule_id=self.rule_id,
            status=MathStatus.SATISFIED,
            reason_code=ReasonCode.TRAJECTORY_RULE_SATISFIED,
            derivation=f"every start has a terminal outcome within {self.max_delay_ms} ms",
            events=events,
            evidence_ids=evidence_ids,
            margin=worst_margin,
            unit="millisecond",
            assumptions=("bounded observation",),
        )


@dataclass(frozen=True)
class TrajectoryVerifier:
    """Validate event evidence, then run ordered deterministic temporal rules."""

    rules: tuple[TrajectoryRule, ...]

    def __post_init__(self) -> None:
        if not self.rules:
            raise MathConfigurationError("TrajectoryVerifier requires at least one rule")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise MathConfigurationError("trajectory rule identifiers must be unique")

    def verify(
        self,
        events: tuple[TrajectoryEvent, ...],
        *,
        complete: bool,
        evidence_ids: tuple[str, ...] = (),
    ) -> MathReport:
        if not isinstance(complete, bool):
            raise MathInputError("complete must be boolean")
        if not events:
            return MathReport(
                (
                    _result(
                        rule_id="trajectory-evidence",
                        status=MathStatus.UNKNOWN,
                        reason_code=ReasonCode.TRAJECTORY_INCOMPLETE,
                        derivation="no trajectory events were supplied",
                        events=events,
                        evidence_ids=evidence_ids,
                        margin=None,
                    ),
                )
            )
        ids: set[str] = set()
        previous_sequence: int | None = None
        previous_time_by_subject: dict[str, int] = {}
        for index, event in enumerate(events):
            if event.event_id in ids:
                witness = _counterexample(
                    "trajectory-evidence",
                    f"event identifier {event.event_id!r} is duplicated",
                    event,
                    index,
                )
                return MathReport(
                    (
                        _result(
                            rule_id="trajectory-evidence",
                            status=MathStatus.VIOLATED,
                            reason_code=ReasonCode.TRAJECTORY_DUPLICATE_EVENT,
                            derivation="event identifiers must be unique",
                            events=events,
                            evidence_ids=evidence_ids,
                            margin=-1,
                            counterexample=witness,
                        ),
                    )
                )
            ids.add(event.event_id)
            subject_time = previous_time_by_subject.get(event.subject_id)
            if (previous_sequence is not None and event.sequence <= previous_sequence) or (
                subject_time is not None and event.occurred_at_ms < subject_time
            ):
                witness = _counterexample(
                    "trajectory-evidence",
                    f"event {event.event_id!r} is not in strict sequence/time order",
                    event,
                    index,
                )
                return MathReport(
                    (
                        _result(
                            rule_id="trajectory-evidence",
                            status=MathStatus.VIOLATED,
                            reason_code=ReasonCode.TRAJECTORY_ORDER_INVALID,
                            derivation=(
                                "global sequence must strictly increase and time must not decrease "
                                "within a subject"
                            ),
                            events=events,
                            evidence_ids=evidence_ids,
                            margin=-1,
                            counterexample=witness,
                        ),
                    )
                )
            previous_sequence = event.sequence
            previous_time_by_subject[event.subject_id] = event.occurred_at_ms
            if not event.authenticated:
                return MathReport(
                    (
                        _result(
                            rule_id="trajectory-authentication",
                            status=MathStatus.UNKNOWN,
                            reason_code=ReasonCode.TRAJECTORY_UNAUTHENTICATED,
                            derivation=f"event {event.event_id!r} is not authenticated evidence",
                            events=events,
                            evidence_ids=evidence_ids,
                            margin=None,
                            assumptions=("unsigned assertions cannot satisfy temporal clauses",),
                        ),
                    )
                )
        return MathReport(
            tuple(
                rule.evaluate(
                    events,
                    complete=complete,
                    evidence_ids=evidence_ids,
                )
                for rule in self.rules
            )
        )
