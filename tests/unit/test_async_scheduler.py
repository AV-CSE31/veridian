from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from veridian.loop.scheduler import AsyncScheduler


def test_empty_task_list_returns_empty_results() -> None:
    scheduler = AsyncScheduler(max_concurrency=3)
    assert asyncio.run(scheduler.run([])) == []


def test_results_preserve_input_order() -> None:
    async def delayed(value: int, delay: float) -> int:
        await asyncio.sleep(delay)
        return value

    scheduler = AsyncScheduler(max_concurrency=5)
    results = asyncio.run(
        scheduler.run([lambda i=i: delayed(i, 0.05 - i * 0.01) for i in range(5)])
    )
    assert results == [0, 1, 2, 3, 4]


def test_concurrency_improves_wall_clock() -> None:
    async def task() -> int:
        await asyncio.sleep(0.05)
        return 1

    scheduler = AsyncScheduler(max_concurrency=5)
    started = time.perf_counter()
    asyncio.run(scheduler.run([task for _ in range(5)]))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.20


def test_max_concurrency_is_honored() -> None:
    async def run_test() -> int:
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def tracked_task() -> None:
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

        scheduler = AsyncScheduler(max_concurrency=2)
        await scheduler.run([tracked_task for _ in range(8)])
        return peak

    assert asyncio.run(run_test()) <= 2


def test_active_count_observes_inflight_tasks() -> None:
    seen: list[int] = []

    async def run_test() -> None:
        scheduler = AsyncScheduler(max_concurrency=3)

        async def task() -> None:
            seen.append(scheduler._active_count)
            await asyncio.sleep(0.02)

        await scheduler.run([task for _ in range(3)])

    asyncio.run(run_test())
    assert any(value > 0 for value in seen)


def test_failure_raises_exception_group_and_cancels_peers() -> None:
    finished: list[str] = []

    async def failing() -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("critical failure")

    async def slow(label: str) -> str:
        await asyncio.sleep(0.5)
        finished.append(label)
        return label

    scheduler = AsyncScheduler(max_concurrency=5)
    with pytest.raises(ExceptionGroup):
        asyncio.run(scheduler.run([failing, lambda: slow("a"), lambda: slow("b")]))

    assert "a" not in finished
    assert "b" not in finished


def test_callback_runs_for_successful_tasks() -> None:
    callback_log: list[tuple[int, Any]] = []

    def on_done(index: int, result: Any) -> None:
        callback_log.append((index, result))

    async def task(value: int) -> int:
        return value * 10

    scheduler = AsyncScheduler(max_concurrency=2, on_task_done=on_done)
    results = asyncio.run(scheduler.run([lambda i=i: task(i) for i in range(3)]))

    assert results == [0, 10, 20]
    assert callback_log == [(0, 0), (1, 10), (2, 20)]


def test_callback_receives_none_for_failed_task() -> None:
    callback_log: list[tuple[int, Any]] = []

    def on_done(index: int, result: Any) -> None:
        callback_log.append((index, result))

    async def failing() -> None:
        raise RuntimeError("boom")

    scheduler = AsyncScheduler(max_concurrency=1, on_task_done=on_done)
    with pytest.raises(ExceptionGroup):
        asyncio.run(scheduler.run([failing]))

    assert callback_log == [(0, None)]


def test_shutdown_is_idempotent() -> None:
    async def run_test() -> None:
        scheduler = AsyncScheduler(max_concurrency=2)
        await scheduler.shutdown()
        await scheduler.shutdown()

    asyncio.run(run_test())
