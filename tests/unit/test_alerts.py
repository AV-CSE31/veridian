"""
tests/unit/test_alerts.py
──────────────────────────
Unit tests for veridian.observability.alerts — Alert rules, manager, and sinks.

Covers:
  - AlertRule triggers when condition is True
  - AlertRule respects cooldown (same alert not re-fired within window)
  - AlertManager routes alerts to sinks
  - LogAlertSink records alerts
  - Severity filtering (only critical, warning+critical, etc.)
"""

from __future__ import annotations

import logging

import pytest

from veridian.observability.alerts import (
    Alert,
    AlertManager,
    AlertRule,
    AlertSeverity,
    AlertSink,
    LogAlertSink,
)

# ─────────────────────────────────────────────────────────────────────────────
# AlertSeverity enum
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertSeverity:
    def test_info_member(self) -> None:
        assert AlertSeverity.INFO.value == "info"

    def test_warning_member(self) -> None:
        assert AlertSeverity.WARNING.value == "warning"

    def test_critical_member(self) -> None:
        assert AlertSeverity.CRITICAL.value == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Alert dataclass
# ─────────────────────────────────────────────────────────────────────────────


class TestAlert:
    def test_alert_creation(self) -> None:
        alert = Alert(
            name="high_failure_rate",
            severity=AlertSeverity.CRITICAL,
            message="Failure rate exceeded threshold",
            fired_at="2026-04-06T12:00:00+00:00",
            details={"current": 0.1, "threshold": 0.05},
        )
        assert alert.name == "high_failure_rate"
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.message == "Failure rate exceeded threshold"
        assert alert.fired_at == "2026-04-06T12:00:00+00:00"
        assert alert.details == {"current": 0.1, "threshold": 0.05}

    def test_alert_with_empty_details(self) -> None:
        alert = Alert(
            name="test",
            severity=AlertSeverity.INFO,
            message="test",
            fired_at="2026-04-06T12:00:00+00:00",
            details={},
        )
        assert alert.details == {}


# ─────────────────────────────────────────────────────────────────────────────
# AlertRule dataclass
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertRule:
    def test_rule_creation(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=300,
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate {failure_rate} exceeds 0.05",
        )
        assert rule.name == "high_failure"
        assert rule.severity == AlertSeverity.CRITICAL
        assert rule.cooldown_seconds == 300

    def test_condition_callable_true(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.WARNING,
            cooldown_seconds=60,
            condition=lambda m: m.get("value", 0) > 10,
            message_template="Value too high",
        )
        assert rule.condition({"value": 15}) is True

    def test_condition_callable_false(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.WARNING,
            cooldown_seconds=60,
            condition=lambda m: m.get("value", 0) > 10,
            message_template="Value too high",
        )
        assert rule.condition({"value": 5}) is False


# ─────────────────────────────────────────────────────────────────────────────
# AlertSink ABC
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertSinkABC:
    def test_alert_sink_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            AlertSink()  # type: ignore[abstract]

    def test_custom_sink_implementation(self) -> None:
        class _TestSink(AlertSink):
            def __init__(self) -> None:
                self.alerts: list[Alert] = []

            def send(self, alert: Alert) -> None:
                self.alerts.append(alert)

        sink = _TestSink()
        alert = Alert(
            name="test",
            severity=AlertSeverity.INFO,
            message="test",
            fired_at="2026-04-06T12:00:00+00:00",
            details={},
        )
        sink.send(alert)
        assert len(sink.alerts) == 1


# ─────────────────────────────────────────────────────────────────────────────
# LogAlertSink
# ─────────────────────────────────────────────────────────────────────────────


class TestLogAlertSink:
    def test_log_alert_sink_is_alert_sink(self) -> None:
        sink = LogAlertSink()
        assert isinstance(sink, AlertSink)

    def test_log_alert_sink_logs_alert(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = LogAlertSink()
        alert = Alert(
            name="high_failure_rate",
            severity=AlertSeverity.CRITICAL,
            message="Failure rate exceeded threshold",
            fired_at="2026-04-06T12:00:00+00:00",
            details={"current": 0.1},
        )
        with caplog.at_level(logging.WARNING):
            sink.send(alert)
        assert any("high_failure_rate" in rec.message for rec in caplog.records)

    def test_log_alert_sink_records_alerts(self) -> None:
        sink = LogAlertSink()
        alert = Alert(
            name="test",
            severity=AlertSeverity.INFO,
            message="test msg",
            fired_at="2026-04-06T12:00:00+00:00",
            details={},
        )
        sink.send(alert)
        assert len(sink.alerts) == 1
        assert sink.alerts[0].name == "test"


# ─────────────────────────────────────────────────────────────────────────────
# AlertManager — basic triggering
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertManagerTrigger:
    def test_fires_alert_when_condition_true(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=0,
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate {failure_rate} exceeds 0.05",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])
        alerts = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts) == 1
        assert alerts[0].name == "high_failure"
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_no_alert_when_condition_false(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=0,
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate {failure_rate} exceeds 0.05",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])
        alerts = manager.evaluate({"failure_rate": 0.01})
        assert len(alerts) == 0

    def test_multiple_rules_fire_independently(self) -> None:
        rules = [
            AlertRule(
                name="high_failure",
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=0,
                condition=lambda m: m.get("failure_rate", 0) > 0.05,
                message_template="Failure rate high",
            ),
            AlertRule(
                name="high_latency",
                severity=AlertSeverity.WARNING,
                cooldown_seconds=0,
                condition=lambda m: m.get("latency", 0) > 30,
                message_template="Latency high",
            ),
        ]
        sink = LogAlertSink()
        manager = AlertManager(rules=rules, sinks=[sink])
        alerts = manager.evaluate({"failure_rate": 0.1, "latency": 50})
        assert len(alerts) == 2

    def test_alert_routed_to_sink(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.INFO,
            cooldown_seconds=0,
            condition=lambda m: True,
            message_template="always fires",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])
        manager.evaluate({"any": 1.0})
        assert len(sink.alerts) == 1

    def test_alert_routed_to_multiple_sinks(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.INFO,
            cooldown_seconds=0,
            condition=lambda m: True,
            message_template="always fires",
        )
        sink1 = LogAlertSink()
        sink2 = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink1, sink2])
        manager.evaluate({"any": 1.0})
        assert len(sink1.alerts) == 1
        assert len(sink2.alerts) == 1


# ─────────────────────────────────────────────────────────────────────────────
# AlertManager — cooldown
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertManagerCooldown:
    def test_cooldown_prevents_refiring(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=600,
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate high",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])

        # First evaluation fires
        alerts1 = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts1) == 1

        # Second evaluation within cooldown does not fire
        alerts2 = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts2) == 0

    def test_cooldown_expires_allows_refiring(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=0,  # instant expiry
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate high",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])

        alerts1 = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts1) == 1

        # With cooldown=0, should fire again
        alerts2 = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts2) == 1

    def test_different_rules_have_independent_cooldowns(self) -> None:
        rules = [
            AlertRule(
                name="rule_a",
                severity=AlertSeverity.WARNING,
                cooldown_seconds=600,
                condition=lambda m: m.get("metric_a", 0) > 1.0,
                message_template="A high",
            ),
            AlertRule(
                name="rule_b",
                severity=AlertSeverity.WARNING,
                cooldown_seconds=0,
                condition=lambda m: m.get("metric_b", 0) > 1.0,
                message_template="B high",
            ),
        ]
        sink = LogAlertSink()
        manager = AlertManager(rules=rules, sinks=[sink])

        # First eval: both fire
        alerts1 = manager.evaluate({"metric_a": 2.0, "metric_b": 2.0})
        assert len(alerts1) == 2

        # Second eval: only rule_b fires (rule_a is in cooldown)
        alerts2 = manager.evaluate({"metric_a": 2.0, "metric_b": 2.0})
        assert len(alerts2) == 1
        assert alerts2[0].name == "rule_b"


# ─────────────────────────────────────────────────────────────────────────────
# AlertManager — severity filtering
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertManagerSeverityFilter:
    def _make_manager(
        self, *, min_severity: AlertSeverity | None = None
    ) -> tuple[AlertManager, LogAlertSink]:
        rules = [
            AlertRule(
                name="info_rule",
                severity=AlertSeverity.INFO,
                cooldown_seconds=0,
                condition=lambda m: True,
                message_template="info alert",
            ),
            AlertRule(
                name="warning_rule",
                severity=AlertSeverity.WARNING,
                cooldown_seconds=0,
                condition=lambda m: True,
                message_template="warning alert",
            ),
            AlertRule(
                name="critical_rule",
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=0,
                condition=lambda m: True,
                message_template="critical alert",
            ),
        ]
        sink = LogAlertSink()
        manager = AlertManager(rules=rules, sinks=[sink], min_severity=min_severity)
        return manager, sink

    def test_no_filter_fires_all(self) -> None:
        manager, sink = self._make_manager()
        alerts = manager.evaluate({"x": 1.0})
        assert len(alerts) == 3

    def test_critical_only_filter(self) -> None:
        manager, sink = self._make_manager(min_severity=AlertSeverity.CRITICAL)
        alerts = manager.evaluate({"x": 1.0})
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_warning_plus_filter(self) -> None:
        manager, sink = self._make_manager(min_severity=AlertSeverity.WARNING)
        alerts = manager.evaluate({"x": 1.0})
        assert len(alerts) == 2
        severities = {a.severity for a in alerts}
        assert severities == {AlertSeverity.WARNING, AlertSeverity.CRITICAL}

    def test_info_filter_includes_all(self) -> None:
        manager, sink = self._make_manager(min_severity=AlertSeverity.INFO)
        alerts = manager.evaluate({"x": 1.0})
        assert len(alerts) == 3


# ─────────────────────────────────────────────────────────────────────────────
# AlertManager — message template formatting
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertManagerMessage:
    def test_message_template_formats_metrics(self) -> None:
        rule = AlertRule(
            name="high_failure",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=0,
            condition=lambda m: m.get("failure_rate", 0) > 0.05,
            message_template="Failure rate {failure_rate} exceeds 0.05",
        )
        sink = LogAlertSink()
        manager = AlertManager(rules=[rule], sinks=[sink])
        alerts = manager.evaluate({"failure_rate": 0.1})
        assert len(alerts) == 1
        assert "0.1" in alerts[0].message

    def test_alert_has_iso_fired_at(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.INFO,
            cooldown_seconds=0,
            condition=lambda m: True,
            message_template="test",
        )
        manager = AlertManager(rules=[rule], sinks=[])
        alerts = manager.evaluate({"x": 1.0})
        assert len(alerts) == 1
        assert "T" in alerts[0].fired_at

    def test_alert_details_contain_metrics(self) -> None:
        rule = AlertRule(
            name="test",
            severity=AlertSeverity.INFO,
            cooldown_seconds=0,
            condition=lambda m: True,
            message_template="test",
        )
        manager = AlertManager(rules=[rule], sinks=[])
        alerts = manager.evaluate({"failure_rate": 0.1})
        assert alerts[0].details.get("failure_rate") == 0.1
