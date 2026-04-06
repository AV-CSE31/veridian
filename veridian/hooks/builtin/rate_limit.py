"""
veridian.hooks.builtin.rate_limit
───────────────────────────────────
RateLimitHook — enforces max tasks per minute via a sliding window.
Priority 50.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = ["RateLimitHook"]

log = logging.getLogger(__name__)


class RateLimitHook(BaseHook):
    """
    Enforces a task-dispatch rate limit using a 60-second sliding window.
    Sleeps in before_task() if the rate limit would be exceeded.
    """

    id: ClassVar[str] = "rate_limit"
    priority: ClassVar[int] = 50

    def __init__(self, max_per_minute: int = 60) -> None:
        self.max_per_minute = max_per_minute
        self._window_seconds = 60.0
        self._calls: deque[float] = deque()

    def before_task(self, event: Any) -> None:
        """Sleep if dispatching now would exceed the per-minute rate limit."""
        now = time.monotonic()
        # Evict timestamps outside the sliding window
        while self._calls and self._calls[0] < now - self._window_seconds:
            self._calls.popleft()

        if len(self._calls) >= self.max_per_minute:
            sleep_for = self._window_seconds - (now - self._calls[0])
            if sleep_for > 0:
                log.info("rate_limit.sleep seconds=%.2f", sleep_for)
                time.sleep(sleep_for)
            # Evict again after sleeping
            now = time.monotonic()
            while self._calls and self._calls[0] < now - self._window_seconds:
                self._calls.popleft()

        self._calls.append(time.monotonic())
