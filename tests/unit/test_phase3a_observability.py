"""
tests.unit.test_phase3a_observability
─────────────────────────────────────
Acceptance tests for Phase 3.A — observability primitives:

* JsonLogFormatter emits one valid JSON object per record, propagating
  extra fields and exception text.
* configure_logging is idempotent and honours
  VERIDIAN_LOG_LEVEL / VERIDIAN_LOG_FORMAT.
* MetricsRegistry counters/gauges/histograms track values per label set
  and the OpenMetrics rendering produces parseable Prometheus output.
* The runner emits cumulative counters + a duration histogram on every
  run().
"""

from __future__ import annotations

import io
import json
import logging
import os
from unittest.mock import patch

import pytest

from veridian.observability.logging_config import (
    JsonLogFormatter,
    configure_logging,
)
from veridian.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    render_openmetrics,
)

# ── JSON log formatter ──────────────────────────────────────────────────────


class TestJsonLogFormatter:
    def _make_record(self, **kwargs: object) -> logging.LogRecord:
        return logging.LogRecord(
            name=str(kwargs.get("name", "veridian.test")),
            level=int(kwargs.get("level", logging.INFO)),  # type: ignore[arg-type]
            pathname=str(kwargs.get("pathname", "test.py")),
            lineno=int(kwargs.get("lineno", 1)),  # type: ignore[arg-type]
            msg=str(kwargs.get("msg", "hello")),
            args=kwargs.get("args"),  # type: ignore[arg-type]
            exc_info=kwargs.get("exc_info"),  # type: ignore[arg-type]
        )

    def test_emits_valid_json(self) -> None:
        fmt = JsonLogFormatter()
        record = self._make_record(msg="hello")
        line = fmt.format(record)
        payload = json.loads(line)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "veridian.test"

    def test_includes_extra_fields(self) -> None:
        fmt = JsonLogFormatter()
        record = self._make_record(msg="claimed")
        record.run_id = "abc123"
        record.task_id = "t-1"
        payload = json.loads(fmt.format(record))
        assert payload["run_id"] == "abc123"
        assert payload["task_id"] == "t-1"

    def test_exception_text_attached(self) -> None:
        fmt = JsonLogFormatter()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys

            record = self._make_record(msg="failed", exc_info=sys.exc_info())
        payload = json.loads(fmt.format(record))
        assert "RuntimeError" in payload["exc"]
        assert "boom" in payload["exc"]


class TestConfigureLogging:
    def test_idempotent_by_default(self) -> None:
        # First call configures; second is a no-op (no duplicate handler).
        configure_logging(level="INFO", json_format=False, force=True)
        before = len(logging.getLogger().handlers)
        configure_logging(level="DEBUG", json_format=False)  # not forced
        after = len(logging.getLogger().handlers)
        assert before == after

    def test_force_replaces_handlers(self) -> None:
        configure_logging(level="INFO", json_format=False, force=True)
        before = len(logging.getLogger().handlers)
        configure_logging(level="INFO", json_format=False, force=True)
        after = len(logging.getLogger().handlers)
        # ``force`` rebuilds; count stays bounded.
        assert after == before

    def test_env_var_selects_json_format(self) -> None:
        buf = io.StringIO()
        env = {"VERIDIAN_LOG_FORMAT": "json"}
        with patch.dict(os.environ, env, clear=False):
            configure_logging(stream=buf, force=True)
            logging.getLogger("veridian.test").info("hello-env")
        # JSON formatter emits a JSON-parseable line.
        last_line = buf.getvalue().strip().splitlines()[-1]
        parsed = json.loads(last_line)
        assert parsed["message"] == "hello-env"


# ── Metrics registry ─────────────────────────────────────────────────────────


class TestMetricsRegistry:
    def test_counter_inc_and_value(self) -> None:
        c = Counter(name="c", description="d")
        c.inc(3)
        c.inc(2, labels={"phase": "a"})
        assert c.value() == 3
        assert c.value(labels={"phase": "a"}) == 2

    def test_counter_rejects_negative(self) -> None:
        c = Counter(name="c", description="d")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_gauge_set_inc_dec(self) -> None:
        g = Gauge(name="g", description="d")
        g.set(5)
        g.inc(2)
        g.dec(1)
        assert g.value() == 6

    def test_histogram_observe_buckets_and_sum(self) -> None:
        h = Histogram(name="h", description="d", buckets=(0.1, 1.0, 10.0))
        for v in [0.05, 0.5, 5.0]:
            h.observe(v)
        samples = dict(h.samples())
        state = samples[()]
        assert state.count == 3
        assert state.sum == pytest.approx(5.55)
        # bucket counts are cumulative-less-than-or-equal: 0.05<=0.1 → bucket 0
        # 0.5<=1.0 → buckets 1
        # 5.0<=10.0 → bucket 2
        assert state.bucket_counts == [1.0, 2.0, 3.0]

    def test_histogram_time_context_manager(self) -> None:
        h = Histogram(name="h", description="d")
        with h.time():
            pass  # near-zero elapsed
        assert dict(h.samples())[()].count == 1

    def test_registry_returns_same_instance_by_name(self) -> None:
        reg = MetricsRegistry()
        a = reg.counter("foo")
        b = reg.counter("foo")
        assert a is b

    def test_render_openmetrics_shape(self) -> None:
        reg = MetricsRegistry()
        reg.counter("foo_total", "a counter").inc(2, {"phase": "x"})
        reg.gauge("bar", "a gauge").set(7)
        text = render_openmetrics(reg)
        assert "# TYPE foo_total counter" in text
        assert 'foo_total{phase="x"} 2' in text
        assert "# TYPE bar gauge" in text
        assert "bar 7" in text


# ── Runner metrics integration ──────────────────────────────────────────────


class TestRunnerEmitsMetrics:
    def test_run_emits_counters_and_histogram(self, tmp_path) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import VeridianRunner
        from veridian.observability.metrics import default_registry
        from veridian.providers.mock_provider import MockProvider

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        provider = MockProvider()

        registry = default_registry()
        before_runs = registry.counter("veridian_runs_total").value({"phase": ""})

        runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
        runner.run()  # 0 tasks → still emits a run counter + histogram

        assert registry.counter("veridian_runs_total").value({"phase": ""}) == before_runs + 1
        # Histogram samples should include at least one observation now.
        histogram_samples = list(registry.histogram("veridian_run_duration_seconds").samples())
        assert any(state.count >= 1 for _labels, state in histogram_samples)
