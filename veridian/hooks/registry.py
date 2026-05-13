"""
veridian.hooks.registry
────────────────────────
HookRegistry — maintains ordered hook list and fires events safely.
fire() wraps every hook call in try/except; one broken hook never kills a run.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from veridian.core.exceptions import ControlFlowSignal
from veridian.hooks.base import BaseHook

__all__ = ["HookRegistry"]

log = logging.getLogger(__name__)


def _metrics_safe() -> tuple[Any, Any] | None:
    """Return the (latency_histogram, error_counter) pair or None.

    Metrics imports go through a try-suppress so the hook registry stays
    usable in deployments that have stripped the observability extra.
    """
    try:
        from veridian.observability.metrics import default_registry  # noqa: PLC0415

        reg = default_registry()
        latency = reg.histogram(
            "veridian_hook_duration_seconds",
            "Per-hook wall-clock duration of a single ``fire`` dispatch.",
        )
        errors = reg.counter(
            "veridian_hook_errors_total",
            "Hook invocations that raised a non-ControlFlowSignal exception.",
        )
        return latency, errors
    except Exception:  # pragma: no cover — metrics must never break a fire
        return None


class HookRegistry:
    """
    Manages registered hooks and dispatches events in ascending priority order.

    CONTRACT (RV3-002):
    - Observability errors (any Exception that is NOT a ControlFlowSignal) are
      caught, logged, and swallowed. The run continues without interruption.
    - Control-flow signals (ControlFlowSignal subclasses, e.g. TaskPauseRequested
      or HumanReviewRequired) are re-raised so the runner can route them to the
      ledger (e.g. ledger.pause()). Without this split, HITL pause-and-resume
      would be impossible because the signal would be swallowed here.

    OBSERVABILITY (Phase 5.A):
    - Every ``fire`` records ``veridian_hook_duration_seconds{hook_id,method}``
      via the metrics registry — operators can spot the hook that's slowing
      a run down.
    - Errors increment ``veridian_hook_errors_total{hook_id,method}``.
    """

    def __init__(self) -> None:
        self._hooks: list[BaseHook] = []

    def register(self, hook: BaseHook) -> None:
        """Add a hook. Registry stays sorted by priority (ascending)."""
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)

    @property
    def hooks(self) -> list[BaseHook]:
        """Read-only view of registered hooks in priority order."""
        return list(self._hooks)

    def fire(self, method: str, event: Any) -> None:
        """
        Call hook.method(event) for each registered hook in priority order.
        If a hook does not implement the method, it is silently skipped.

        Exception handling (RV3-002):
        - ControlFlowSignal subclasses (TaskPauseRequested, HumanReviewRequired,
          ...) are re-raised immediately. Subsequent hooks are NOT called.
        - All other exceptions are caught, logged, and swallowed so one broken
          observability hook can never kill a run.
        """
        metrics = _metrics_safe()
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            hook_id = getattr(hook, "id", "?")
            start = time.perf_counter()
            try:
                fn(event)
            except ControlFlowSignal:
                # Control-flow signals MUST propagate to the runner — do NOT
                # swallow them here or HITL pause/resume becomes a no-op.
                raise
            except Exception as exc:
                if metrics is not None:
                    _latency, errors = metrics
                    with contextlib.suppress(Exception):  # pragma: no cover
                        errors.inc(labels={"hook_id": str(hook_id), "method": method})
                log.error(
                    "hook.error hook_id=%s method=%s err=%s",
                    hook_id,
                    method,
                    exc,
                    exc_info=True,
                )
            finally:
                if metrics is not None:
                    latency, _errors = metrics
                    with contextlib.suppress(Exception):  # pragma: no cover
                        latency.observe(
                            time.perf_counter() - start,
                            labels={"hook_id": str(hook_id), "method": method},
                        )
