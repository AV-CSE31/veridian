"""
veridian.hooks.registry
────────────────────────
HookRegistry — maintains ordered hook list and fires events safely.
fire() wraps every hook call in try/except; one broken hook never kills a run.
"""

from __future__ import annotations

import logging
from typing import Any

from veridian.core.exceptions import ControlFlowSignal
from veridian.hooks.base import BaseHook

__all__ = ["HookRegistry"]

log = logging.getLogger(__name__)


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
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            try:
                fn(event)
            except ControlFlowSignal:
                # Control-flow signals MUST propagate to the runner — do NOT
                # swallow them here or HITL pause/resume becomes a no-op.
                raise
            except Exception as exc:
                log.error(
                    "hook.error hook_id=%s method=%s err=%s",
                    getattr(hook, "id", "?"),
                    method,
                    exc,
                    exc_info=True,
                )
