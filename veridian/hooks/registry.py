"""Hook registry with isolated hook failures."""

from __future__ import annotations

import logging
from typing import Any

from veridian.core.exceptions import ControlFlowSignal, HardControlViolation
from veridian.hooks.base import BaseHook

__all__ = ["HookRegistry"]

log = logging.getLogger(__name__)


class HookRegistry:
    """Maintain hooks and dispatch events in ascending priority order."""

    def __init__(self) -> None:
        self._hooks: list[BaseHook] = []

    def register(self, hook: BaseHook) -> None:
        """Add a hook and keep the registry sorted by priority."""
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)

    @property
    def hooks(self) -> list[BaseHook]:
        """Return registered hooks in priority order."""
        return list(self._hooks)

    def fire(self, method: str, event: Any) -> None:
        """Call hook.method(event) for each registered hook.

        Control-flow signals and hard-control violations propagate to the
        runner. All other hook errors are logged and swallowed so a broken
        observational hook cannot kill a run.
        """
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            hook_id = getattr(hook, "id", "?")
            try:
                fn(event)
            except (ControlFlowSignal, HardControlViolation):
                raise
            except Exception as exc:
                log.error(
                    "hook.error hook_id=%s method=%s err=%s",
                    hook_id,
                    method,
                    exc,
                    exc_info=True,
                )
