"""
veridian.observability.metrics
──────────────────────────────
Minimal, stdlib-only metrics registry for production deployments.

Provides three primitives that map cleanly onto Prometheus / OpenMetrics
when exposed by a dashboard or sidecar exporter:

* :class:`Counter` — monotonically increasing total.
* :class:`Gauge` — point-in-time numeric value.
* :class:`Histogram` — bucketed observation count + sum.

The registry is process-local, thread-safe, and never raises on
malformed labels — it favours operational simplicity over strict
type enforcement. Callers can dump everything via
:func:`render_openmetrics` for an exposition endpoint.

The Veridian runner increments these from its task loop (see
``runner.py`` ``_emit_metrics``). Dashboards or sidecars expose the
registry over HTTP; this module deliberately does not import any web
framework so it can run in the lightest deployment.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Counter",
    "DEFAULT_HISTOGRAM_BUCKETS",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "default_registry",
    "render_openmetrics",
]


# Buckets chosen for typical agent/task latencies: 5ms → 60s.
DEFAULT_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Canonical, hashable representation of a label set."""
    if not labels:
        return ()
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


@dataclass
class Counter:
    """Monotonically increasing total. Use for ``task_completed_total`` etc."""

    name: str
    description: str
    _values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        if amount < 0:
            raise ValueError("Counter.inc(amount) must be >= 0")
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._values.get(_label_key(labels), 0.0)

    def samples(self) -> Iterable[tuple[tuple[tuple[str, str], ...], float]]:
        with self._lock:
            return list(self._values.items())


@dataclass
class Gauge:
    """Point-in-time value. Use for ``queue_depth``, ``circuit_breaker_open``."""

    name: str
    description: str
    _values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._values[_label_key(labels)] = float(value)

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        self.inc(-amount, labels)

    def value(self, labels: dict[str, str] | None = None) -> float:
        with self._lock:
            return self._values.get(_label_key(labels), 0.0)

    def samples(self) -> Iterable[tuple[tuple[tuple[str, str], ...], float]]:
        with self._lock:
            return list(self._values.items())


@dataclass
class _HistogramState:
    bucket_counts: list[float]
    sum: float = 0.0
    count: int = 0


@dataclass
class Histogram:
    """Bucketed observation count + running sum. Use for latencies."""

    name: str
    description: str
    buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS
    _states: dict[tuple[tuple[str, str], ...], _HistogramState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _HistogramState(bucket_counts=[0.0] * len(self.buckets))
                self._states[key] = state
            for idx, upper in enumerate(self.buckets):
                if value <= upper:
                    state.bucket_counts[idx] += 1
            state.sum += value
            state.count += 1

    def time(self, labels: dict[str, str] | None = None) -> _HistogramTimer:
        """Context manager that records elapsed seconds on exit."""
        return _HistogramTimer(self, labels)

    def samples(
        self,
    ) -> Iterable[tuple[tuple[tuple[str, str], ...], _HistogramState]]:
        with self._lock:
            return list(self._states.items())


class _HistogramTimer:
    __slots__ = ("histogram", "labels", "_start")

    def __init__(self, histogram: Histogram, labels: dict[str, str] | None) -> None:
        self.histogram = histogram
        self.labels = labels
        self._start = 0.0

    def __enter__(self) -> _HistogramTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.histogram.observe(time.perf_counter() - self._start, self.labels)


class MetricsRegistry:
    """Process-local metrics registry, thread-safe.

    Use :func:`default_registry` for a shared instance; tests should
    construct their own to avoid state bleed.
    """

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, description: str = "") -> Counter:
        with self._lock:
            metric = self._counters.get(name)
            if metric is None:
                metric = Counter(name=name, description=description)
                self._counters[name] = metric
            return metric

    def gauge(self, name: str, description: str = "") -> Gauge:
        with self._lock:
            metric = self._gauges.get(name)
            if metric is None:
                metric = Gauge(name=name, description=description)
                self._gauges[name] = metric
            return metric

    def histogram(
        self,
        name: str,
        description: str = "",
        buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS,
    ) -> Histogram:
        with self._lock:
            metric = self._histograms.get(name)
            if metric is None:
                metric = Histogram(name=name, description=description, buckets=buckets)
                self._histograms[name] = metric
            return metric

    def all_metrics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": dict(self._histograms),
            }


_DEFAULT = MetricsRegistry()


def default_registry() -> MetricsRegistry:
    """Shared process-local registry. Veridian's runner uses this by default."""
    return _DEFAULT


# ── OpenMetrics exposition ──────────────────────────────────────────────────


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + body + "}"


def render_openmetrics(registry: MetricsRegistry | None = None) -> str:
    """Render the registry as an OpenMetrics-compatible text exposition.

    The output is suitable for serving from a ``/metrics`` endpoint —
    Prometheus, Grafana Agent, and Datadog OpenMetrics scrapers all parse
    the same dialect.
    """
    reg = registry or _DEFAULT
    snapshot = reg.all_metrics()
    lines: list[str] = []

    for counter in snapshot["counters"].values():
        if counter.description:
            lines.append(f"# HELP {counter.name} {counter.description}")
        lines.append(f"# TYPE {counter.name} counter")
        for labels, value in counter.samples():
            lines.append(f"{counter.name}{_format_labels(labels)} {value}")

    for gauge in snapshot["gauges"].values():
        if gauge.description:
            lines.append(f"# HELP {gauge.name} {gauge.description}")
        lines.append(f"# TYPE {gauge.name} gauge")
        for labels, value in gauge.samples():
            lines.append(f"{gauge.name}{_format_labels(labels)} {value}")

    for hist in snapshot["histograms"].values():
        if hist.description:
            lines.append(f"# HELP {hist.name} {hist.description}")
        lines.append(f"# TYPE {hist.name} histogram")
        for labels, state in hist.samples():
            for upper, count in zip(hist.buckets, state.bucket_counts, strict=False):
                bucket_labels = (*labels, ("le", str(upper)))
                lines.append(f"{hist.name}_bucket{_format_labels(bucket_labels)} {count}")
            inf_labels = (*labels, ("le", "+Inf"))
            lines.append(f"{hist.name}_bucket{_format_labels(inf_labels)} {state.count}")
            lines.append(f"{hist.name}_sum{_format_labels(labels)} {state.sum}")
            lines.append(f"{hist.name}_count{_format_labels(labels)} {state.count}")

    lines.append("")  # trailing newline for OpenMetrics conformance
    return "\n".join(lines)
