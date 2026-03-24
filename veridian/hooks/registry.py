"""
veridian.hooks.registry
────────────────────────
HookRegistry — maintains ordered hook list and fires events safely.
fire() wraps every hook call in try/except; one broken hook never kills a run.
"""
from __future__ import annotations

import logging
from typing import Any

from veridian.hooks.base import BaseHook

__all__ = ["HookRegistry"]

log = logging.getLogger(__name__)


class HookRegistry:
    """
    Manages registered hooks and dispatches events in ascending priority order.

    CONTRACT: A failing hook NEVER propagates — exceptions are caught, logged,
    and swallowed. The run continues without interruption.
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
        Exceptions from hooks are caught, logged, and never re-raised.
        """
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            try:
                fn(event)
            except Exception as exc:
                log.error(
                    "hook.error hook_id=%s method=%s err=%s",
                    getattr(hook, "id", "?"),
                    method,
                    exc,
                    exc_info=True,
                )
