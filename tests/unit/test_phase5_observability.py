"""
tests.unit.test_phase5_observability
────────────────────────────────────
Acceptance tests for Phase 5 — production observability:

* Phase 5.A: ``HookRegistry.fire`` records hook-duration and hook-error
  metrics, and continues firing after a hook raises.
* Phase 5.B: ``LiteLLMProvider.complete`` records provider-latency and
  token-total metrics (best-effort smoke test against MockProvider
  doesn't apply — exercise the helper directly).
* Phase 5.C: ``VeridianRunner._emit_verifier_metrics`` and the run-end
  queue-depth gauge update the shared registry.
"""

from __future__ import annotations

from typing import Any

import pytest

from veridian.core.task import Task
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry
from veridian.observability.metrics import MetricsRegistry


def _swap_registry(monkeypatch: pytest.MonkeyPatch) -> MetricsRegistry:
    """Replace the shared default registry with a fresh one so per-test
    counters don't bleed into each other."""
    import veridian.observability.metrics as _m

    fresh = MetricsRegistry()
    monkeypatch.setattr(_m, "_DEFAULT", fresh)
    return fresh


# ── Phase 5.A: Hook observability ────────────────────────────────────────────


class _RecordingHook(BaseHook):
    id = "recording"
    priority = 50

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def after_task(self, event: Any) -> None:
        self.calls.append(event)


class _ExplodingHook(BaseHook):
    id = "exploding"
    priority = 60

    def after_task(self, event: Any) -> None:
        raise RuntimeError("boom in hook")


class TestHookMetrics:
    def test_fire_records_duration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _swap_registry(monkeypatch)
        hooks = HookRegistry()
        recorder = _RecordingHook()
        hooks.register(recorder)

        hooks.fire("after_task", event=object())

        samples = dict(reg.histogram("veridian_hook_duration_seconds").samples())
        assert any(
            ("hook_id", "recording") in labels and ("method", "after_task") in labels
            for labels in samples
        )
        # The recorder still saw the event.
        assert len(recorder.calls) == 1

    def test_exception_increments_error_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _swap_registry(monkeypatch)
        hooks = HookRegistry()
        hooks.register(_ExplodingHook())
        # Co-registered hook still fires after the explosion (existing
        # contract, re-pinned here).
        recorder = _RecordingHook()
        hooks.register(recorder)

        hooks.fire("after_task", event=object())

        assert (
            reg.counter("veridian_hook_errors_total").value(
                labels={"hook_id": "exploding", "method": "after_task"}
            )
            == 1.0
        )
        # Co-registered hook still ran after the failure.
        assert len(recorder.calls) == 1


# ── Phase 5.B: Provider metrics helper ───────────────────────────────────────


class TestProviderMetricsHelper:
    def test_success_records_latency_and_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _swap_registry(monkeypatch)
        from veridian.providers.litellm_provider import _emit_provider_metrics

        _emit_provider_metrics(
            model="gemini/gemini-2.5-flash",
            outcome="success",
            duration=0.15,
            input_tokens=120,
            output_tokens=42,
        )
        latency = dict(reg.histogram("veridian_provider_latency_seconds").samples())
        assert any(
            ("model", "gemini/gemini-2.5-flash") in labels and ("outcome", "success") in labels
            for labels in latency
        )
        tokens = reg.counter("veridian_provider_tokens_total")
        assert tokens.value({"model": "gemini/gemini-2.5-flash", "direction": "input"}) == 120
        assert tokens.value({"model": "gemini/gemini-2.5-flash", "direction": "output"}) == 42

    def test_error_records_latency_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _swap_registry(monkeypatch)
        from veridian.providers.litellm_provider import _emit_provider_metrics

        _emit_provider_metrics(model="gpt-4o", outcome="error", duration=2.5)
        latency_samples = dict(reg.histogram("veridian_provider_latency_seconds").samples())
        assert any(
            ("model", "gpt-4o") in labels and ("outcome", "error") in labels
            for labels in latency_samples
        )
        # No token counter entries created when zero tokens passed.
        assert not list(reg.counter("veridian_provider_tokens_total").samples())


# ── Phase 5.C: Verifier metrics + queue-depth gauge ──────────────────────────


class TestVerifierMetrics:
    def test_pass_outcome_records_counter_and_histogram(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import VeridianRunner
        from veridian.providers.mock_provider import MockProvider

        reg = _swap_registry(monkeypatch)

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        runner = VeridianRunner(ledger=ledger, provider=MockProvider(), config=config)

        runner._emit_verifier_metrics(verifier_id="schema", outcome="pass", duration=0.01)
        runner._emit_verifier_metrics(verifier_id="schema", outcome="fail", duration=0.02)

        invocations = reg.counter("veridian_verifier_invocations_total")
        assert invocations.value({"verifier_id": "schema", "outcome": "pass"}) == 1
        assert invocations.value({"verifier_id": "schema", "outcome": "fail"}) == 1


class TestQueueDepthGauge:
    def test_gauge_set_on_run_emit(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import RunSummary, VeridianRunner
        from veridian.providers.mock_provider import MockProvider

        reg = _swap_registry(monkeypatch)

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        # Seed 3 pending tasks so the gauge has something to report.
        ledger.add([Task(title=f"t{i}", verifier_id="schema") for i in range(3)])
        runner = VeridianRunner(ledger=ledger, provider=MockProvider(), config=config)

        summary = RunSummary(run_id="r1", duration_seconds=0.5, phase=None)
        runner._emit_run_metrics(summary, phase=None)

        depth = reg.gauge("veridian_queue_depth").value({"phase": ""})
        assert depth == 3.0
