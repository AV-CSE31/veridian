"""
veridian.loop.scheduler
-----------------------
AsyncScheduler with bounded concurrency for coroutine execution.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["AsyncScheduler"]

log = logging.getLogger(__name__)


class AsyncScheduler:
    """Schedule coroutine callables with bounded concurrency."""

    def __init__(
        self,
        max_concurrency: int = 5,
        on_task_done: Callable[[int, Any], None] | None = None,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._on_task_done = on_task_done
        self._semaphore: asyncio.Semaphore | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._task_group_tasks: list[asyncio.Task[Any]] = []
        self._active = 0

    @property
    def _active_count(self) -> int:
        """Current number of in-flight tasks within the semaphore."""
        return self._active

    async def run(self, tasks: list[Callable[..., Awaitable[Any]]]) -> list[Any]:
        """Run tasks and return ordered results matching input indices."""
        if not tasks:
            return []

        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._shutdown_event = asyncio.Event()
        self._task_group_tasks = []
        results: list[Any] = [None] * len(tasks)

        async def _guarded(index: int, coro_fn: Callable[..., Awaitable[Any]]) -> None:
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                return
            semaphore = self._semaphore
            assert semaphore is not None  # noqa: S101
            async with semaphore:
                self._active += 1
                try:
                    result = await coro_fn()
                    results[index] = result
                    if self._on_task_done is not None:
                        self._on_task_done(index, result)
                except BaseException:
                    if self._on_task_done is not None:
                        self._on_task_done(index, None)
                    raise
                finally:
                    self._active -= 1

        async with asyncio.TaskGroup() as tg:
            for idx, task_fn in enumerate(tasks):
                task = tg.create_task(_guarded(idx, task_fn))
                self._task_group_tasks.append(task)

        return results

    async def shutdown(self) -> None:
        """Cancel pending tasks and request graceful stop."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        for task in self._task_group_tasks:
            if not task.done():
                task.cancel()
        self._task_group_tasks = []

        log.info("async_scheduler.shutdown requested")
