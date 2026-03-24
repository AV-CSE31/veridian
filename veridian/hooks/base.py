"""
veridian.hooks.base
───────────────────
BaseHook ABC. All hooks inherit from this.
All lifecycle methods default to no-op — subclasses override only what they need.
Priority is a ClassVar[int]; lower numbers run earlier.
"""
from __future__ import annotations

from typing import Any, ClassVar

__all__ = ["BaseHook"]


class BaseHook:
    """
    Abstract base hook. Override only the lifecycle methods you need.
    Hook errors are swallowed by HookRegistry.fire() — they never propagate.

    Priority convention (ClassVar[int]):
      logging_hook = 0   (runs first — always sees the unmodified state)
      identity_guard = 5
      all others = 50
    """

    id: ClassVar[str] = ""
    priority: ClassVar[int] = 50  # lower = runs earlier

    def before_run(self, event: Any) -> None:
        """Called once before the runner starts processing tasks."""

    def after_run(self, event: Any) -> None:
        """Called once after the runner finishes (normal or aborted)."""

    def before_task(self, event: Any) -> None:
        """Called immediately before a task is dispatched to the worker agent."""

    def after_task(self, event: Any) -> None:
        """Called after a task completes (pass or fail) and the ledger is updated."""

    def on_failure(self, event: Any) -> None:
        """Called when a task transitions to FAILED or ABANDONED."""
