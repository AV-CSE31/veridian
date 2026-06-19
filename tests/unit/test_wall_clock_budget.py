"""
tests.unit.test_wall_clock_budget
---------------------------------------------------------------
Fitment follow-up item 2: WallClockBudgetHook fires a RunAbortRequested
when total elapsed run time exceeds ``max_seconds``.
"""

from __future__ import annotations

import time

import pytest

from veridian.core.events import RunStarted, TaskClaimed
from veridian.core.exceptions import RunAbortRequested, WallClockBudgetExceeded
from veridian.hooks.builtin.wall_clock_budget import WallClockBudgetHook


def test_rejects_non_positive_budget() -> None:
    with pytest.raises(ValueError):
        WallClockBudgetHook(max_seconds=0)
    with pytest.raises(ValueError):
        WallClockBudgetHook(max_seconds=-1)


def test_under_budget_does_not_raise() -> None:
    hook = WallClockBudgetHook(max_seconds=60.0)
    hook.before_run(RunStarted(run_id="r1", total_tasks=1))
    hook.before_task(TaskClaimed(run_id="r1"))


def test_over_budget_raises_wall_clock_exceeded() -> None:
    hook = WallClockBudgetHook(max_seconds=0.05)
    hook.before_run(RunStarted(run_id="r1", total_tasks=1))
    time.sleep(0.07)
    with pytest.raises(WallClockBudgetExceeded) as exc_info:
        hook.before_task(TaskClaimed(run_id="r1"))
    assert isinstance(exc_info.value, RunAbortRequested)
    assert exc_info.value.limit == pytest.approx(0.05)
    assert exc_info.value.elapsed >= 0.05


def test_lazy_start_when_before_run_not_called() -> None:
    """If before_run was skipped, clock starts on first before_task."""
    hook = WallClockBudgetHook(max_seconds=1.0)
    hook.before_task(TaskClaimed(run_id="r1"))
    assert hook._start is not None
    assert hook.elapsed_seconds >= 0.0
