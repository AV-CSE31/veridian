"""
veridian.hooks.builtin.wall_clock_budget
---------------------------------------------------------------
WallClockBudgetHook --- halts the run when total wall-clock duration
exceeds ``max_seconds``. Priority 40, so it fires before per-task hooks.

Routes through :class:`veridian.core.exceptions.WallClockBudgetExceeded`,
which is a :class:`RunAbortRequested` subclass. The dispatcher catches
this in the outer loop and breaks rather than pausing a single task.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from veridian.core.exceptions import WallClockBudgetExceeded
from veridian.hooks.base import BaseHook

__all__ = ["WallClockBudgetHook"]


class WallClockBudgetHook(BaseHook):
    """Hard run-level wall-clock cap.

    The clock starts on ``before_run`` (or, lazily, on the first
    ``before_task``). Subsequent ``before_task`` calls compare elapsed
    seconds against ``max_seconds`` and raise if the budget is exhausted.
    """

    id: ClassVar[str] = "wall_clock_budget"
    priority: ClassVar[int] = 40

    def __init__(self, max_seconds: float) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.max_seconds = float(max_seconds)
        self._start: float | None = None

    def before_run(self, event: Any) -> None:
        self._start = time.monotonic()

    def before_task(self, event: Any) -> None:
        if self._start is None:
            self._start = time.monotonic()
            return
        elapsed = time.monotonic() - self._start
        if elapsed >= self.max_seconds:
            raise WallClockBudgetExceeded(elapsed, self.max_seconds)

    @property
    def elapsed_seconds(self) -> float:
        if self._start is None:
            return 0.0
        return time.monotonic() - self._start
