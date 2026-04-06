from __future__ import annotations

import json
from pathlib import Path

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.testing.fault_injector import (
    FaultInjector,
    FaultSchedule,
    FaultType,
    InjectedCrash,
)


def _make_task(title: str = "crash-test-task") -> Task:
    return Task(title=title, description="Task for crash recovery stress test")


def _simulate_execution_with_crash(
    ledger: TaskLedger,
    task: Task,
    injector: FaultInjector,
    crash_at_step: int,
    total_steps: int = 5,
) -> bool:
    run_id = ledger.run_id
    if task.id not in {item.id for item in ledger.list()}:
        ledger.add([task])
    ledger.claim(task.id, runner_id=run_id)

    for step in range(total_steps):
        try:
            injector.inject_at_step(step, target_step=crash_at_step)
        except InjectedCrash:
            return False

    result = TaskResult(raw_output="done", structured={"completed": True})
    ledger.submit_result(task.id, result)
    ledger.mark_done(task.id, result)
    return True


def test_ledger_file_remains_valid_json_after_injected_crash(tmp_path: Path) -> None:
    ledger = TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    task = _make_task()
    injector = FaultInjector(
        FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])
    )

    assert not _simulate_execution_with_crash(ledger, task, injector, crash_at_step=2)

    raw = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert "tasks" in raw
    assert task.id in raw["tasks"]


def test_reset_in_progress_after_crash_returns_task_to_pending(tmp_path: Path) -> None:
    ledger = TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    task = _make_task()
    injector = FaultInjector(
        FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])
    )

    _simulate_execution_with_crash(ledger, task, injector, crash_at_step=1)
    assert ledger.get(task.id).status == TaskStatus.IN_PROGRESS

    assert ledger.reset_in_progress() == 1
    assert ledger.get(task.id).status == TaskStatus.PENDING


def test_resume_after_crash_can_complete_task(tmp_path: Path) -> None:
    ledger = TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    task = _make_task()
    crash_injector = FaultInjector(
        FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])
    )

    _simulate_execution_with_crash(ledger, task, crash_injector, crash_at_step=2)
    assert ledger.get(task.id).status == TaskStatus.IN_PROGRESS

    ledger.reset_in_progress()
    assert ledger.get(task.id).status == TaskStatus.PENDING

    no_fault_injector = FaultInjector(FaultSchedule(seed=7, fault_probability=0.0))
    assert _simulate_execution_with_crash(
        ledger,
        task,
        no_fault_injector,
        crash_at_step=999,
        total_steps=5,
    )
    assert ledger.get(task.id).status == TaskStatus.DONE
