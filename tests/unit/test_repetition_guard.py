"""
tests.unit.test_repetition_guard
---------------------------------------------------------------
Fitment follow-up item 3: RepetitionGuardHook trips when N consecutive
task outputs hash identically.
"""

from __future__ import annotations

import pytest

from veridian.core.events import TaskCompleted
from veridian.core.exceptions import RepetitionDetected, RunAbortRequested
from veridian.core.task import Task, TaskResult
from veridian.hooks.builtin.repetition_guard import RepetitionGuardHook


def _completed(result: TaskResult, task_id: str = "t1") -> TaskCompleted:
    task = Task(title=task_id)
    task.result = result
    return TaskCompleted(run_id="r1", task=task, result=result)


def test_window_must_be_at_least_two() -> None:
    with pytest.raises(ValueError):
        RepetitionGuardHook(window=1)


def test_does_not_trip_below_window() -> None:
    hook = RepetitionGuardHook(window=3)
    r = TaskResult(raw_output="same", structured={"x": 1})
    hook.after_task(_completed(r))
    hook.after_task(_completed(r))
    # third occurrence required to trip


def test_trips_on_identical_structured_outputs() -> None:
    hook = RepetitionGuardHook(window=3)
    r = TaskResult(raw_output="same", structured={"verdict": "PASS", "n": 1})
    hook.after_task(_completed(r))
    hook.after_task(_completed(r))
    with pytest.raises(RepetitionDetected) as exc_info:
        hook.after_task(_completed(r))
    assert isinstance(exc_info.value, RunAbortRequested)
    assert exc_info.value.window == 3


def test_does_not_trip_on_varying_structured_outputs() -> None:
    hook = RepetitionGuardHook(window=3)
    for i in range(5):
        r = TaskResult(raw_output="x", structured={"step": i})
        hook.after_task(_completed(r))


def test_falls_back_to_raw_output_when_structured_empty() -> None:
    hook = RepetitionGuardHook(window=2)
    r = TaskResult(raw_output="identical body", structured={})
    hook.after_task(_completed(r))
    with pytest.raises(RepetitionDetected):
        hook.after_task(_completed(r))


def test_empty_outputs_are_skipped() -> None:
    """A blank fingerprint never trips the guard."""
    hook = RepetitionGuardHook(window=2)
    for _ in range(5):
        r = TaskResult(raw_output="", structured={})
        hook.after_task(_completed(r))


def test_no_op_when_event_lacks_task() -> None:
    hook = RepetitionGuardHook(window=2)
    hook.after_task(TaskCompleted(run_id="r1"))  # task is None, must not raise
