"""
veridian.observability.alerts
─────────────────────────────
Alert rules, severity routing, and pluggable sinks for the Veridian dashboard.

Rules:
- AlertSink is an ABC — concrete sinks implement ``send(alert)``.
- AlertManager tracks per-rule cooldowns via ``_last_fired`` timestamps.
- Severity filtering: ``min_severity`` on AlertManager drops lower-priority alerts.
- LogAlertSink keeps an in-memory ``alerts`` list AND logs via the ``logging`` module.
"""

from __future__ import annotations

import abc
import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)

__all__ = [
    "Alert",
    "AlertManager",
    "AlertRule",
    "AlertSeverity",
    "AlertSink",
    "LogAlertSink",
]

# ── Severity ordering (low → high) ──────────────────────────────────────────

_SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}


# ── Enums ────────────────────────────────────────────────────────────────────


class AlertSeverity(enum.Enum):
    """Alert severity levels (ascending order)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alert:
    """A single fired alert."""

    name: str
    severity: AlertSeverity
    message: str
    fired_at: str
    details: dict[str, float]


@dataclass
class AlertRule:
    """Defines when and how an alert fires.

    Attributes
    ----------
    name : str
        Unique rule identifier.
    severity : AlertSeverity
        Severity of alerts produced by this rule.
    cooldown_seconds : int
        Minimum seconds between consecutive firings of this rule.
    condition : Callable[[dict[str, float]], bool]
        Returns ``True`` when the alert should fire given current metrics.
    message_template : str
        Python ``str.format_map`` template interpolated with the metrics dict.
    """

    name: str
    severity: AlertSeverity
    cooldown_seconds: int
    condition: Callable[[dict[str, float]], bool]
    message_template: str


# ── Sink ABC ─────────────────────────────────────────────────────────────────


class AlertSink(abc.ABC):
    """Abstract base class for alert delivery targets."""

    @abc.abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver *alert* to its destination."""


# ── LogAlertSink ─────────────────────────────────────────────────────────────


class LogAlertSink(AlertSink):
    """Logs alerts via the ``logging`` module and keeps an in-memory record.

    Attributes
    ----------
    alerts : list[Alert]
        All alerts delivered through this sink since creation.
    """

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        """Log the alert and append to the in-memory list."""
        self.alerts.append(alert)
        log.warning(
            "ALERT [%s] %s: %s",
            alert.severity.value.upper(),
            alert.name,
            alert.message,
        )


# ── AlertManager ─────────────────────────────────────────────────────────────


class AlertManager:
    """Evaluate alert rules against metrics, enforce cooldowns, route to sinks.

    Parameters
    ----------
    rules : list[AlertRule]
        Rules to evaluate on each ``evaluate()`` call.
    sinks : list[AlertSink]
        Sinks that receive every fired alert.
    min_severity : AlertSeverity | None
        If set, alerts below this severity are silently dropped.
    """

    def __init__(
        self,
        rules: list[AlertRule],
        sinks: list[AlertSink],
        min_severity: AlertSeverity | None = None,
    ) -> None:
        self._rules = list(rules)
        self._sinks = list(sinks)
        self._min_severity = min_severity
        self._last_fired: dict[str, float] = {}

    def evaluate(self, metrics: dict[str, float]) -> list[Alert]:
        """Check all rules against *metrics*.

        Returns
        -------
        list[Alert]
            Alerts that were fired (after cooldown and severity filtering).
        """
        now = time.monotonic()
        now_iso = datetime.now(tz=UTC).isoformat()
        fired: list[Alert] = []

        for rule in self._rules:
            # Severity filter
            if (
                self._min_severity is not None
                and _SEVERITY_ORDER[rule.severity.value] < _SEVERITY_ORDER[self._min_severity.value]
            ):
                continue

            # Cooldown check
            last = self._last_fired.get(rule.name)
            if last is not None and (now - last) < rule.cooldown_seconds:
                continue

            # Condition check
            if not rule.condition(metrics):
                continue

            # Fire alert
            try:
                message = rule.message_template.format_map(metrics)
            except KeyError:
                message = rule.message_template

            alert = Alert(
                name=rule.name,
                severity=rule.severity,
                message=message,
                fired_at=now_iso,
                details=dict(metrics),
            )

            self._last_fired[rule.name] = now
            fired.append(alert)

            # Route to sinks
            for sink in self._sinks:
                try:
                    sink.send(alert)
                except Exception:
                    log.exception(
                        "Sink %s failed to deliver alert %s", type(sink).__name__, rule.name
                    )

        return fired
