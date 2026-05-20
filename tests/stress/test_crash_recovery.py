from __future__ import annotations

import json
from pathlib import Path

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger


def _make_task(title: str = "crash-test-task") -> Task:
    return Task(title=title, description="Task for crash recovery stress test")


def _simulate_execution_with_crash(
    ledger: TaskLedger,
    task: Task,
    crash_at_step: int,
    total_steps: int = 5,
) -> bool:
    run_id = ledger.run_id
    if task.id not in {item.id for item in ledger.list()}:
        ledger.add([task])
    ledger.claim(task.id, runner_id=run_id)

    for step in range(total_steps):
        if step == crash_at_step:
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

    assert not _simulate_execution_with_crash(ledger, task, crash_at_step=2)

    raw = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert "tasks" in raw
    assert task.id in raw["tasks"]


def test_reset_in_progress_after_crash_returns_task_to_pending(tmp_path: Path) -> None:
    ledger = TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    task = _make_task()

    _simulate_execution_with_crash(ledger, task, crash_at_step=1)
    assert ledger.get(task.id).status == TaskStatus.IN_PROGRESS

    assert ledger.reset_in_progress() == 1
    assert ledger.get(task.id).status == TaskStatus.PENDING


def test_resume_after_crash_can_complete_task(tmp_path: Path) -> None:
    ledger = TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    task = _make_task()

    _simulate_execution_with_crash(ledger, task, crash_at_step=2)
    assert ledger.get(task.id).status == TaskStatus.IN_PROGRESS

    ledger.reset_in_progress()
    assert ledger.get(task.id).status == TaskStatus.PENDING

    assert _simulate_execution_with_crash(
        ledger,
        task,
        crash_at_step=999,
        total_steps=5,
    )
    assert ledger.get(task.id).status == TaskStatus.DONE
