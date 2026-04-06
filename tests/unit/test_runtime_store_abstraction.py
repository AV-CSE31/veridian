"""RuntimeStore protocol regression tests.

Ensures runners can operate against a storage object that is *not* a concrete
TaskLedger instance, as long as it satisfies the RuntimeStore contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider


class ForwardingRuntimeStore:
    """Simple RuntimeStore-compatible wrapper around TaskLedger."""

    def __init__(self, inner: TaskLedger) -> None:
        self._inner = inner

    def get(self, task_id: str) -> Task:
        return self._inner.get(task_id)

    def get_next(
        self,
        phase: str | None = None,
        respect_dependencies: bool = True,
        include_paused: bool = False,
    ) -> Task | None:
        return self._inner.get_next(
            phase=phase,
            respect_dependencies=respect_dependencies,
            include_paused=include_paused,
        )

    def list(
        self,
        status: TaskStatus | str | None = None,
        phase: str | None = None,
        priority_gte: int | None = None,
    ) -> list[Task]:
        return self._inner.list(status=status, phase=phase, priority_gte=priority_gte)

    def stats(self) -> Any:
        return self._inner.stats()

    def phases(self) -> list[str]:
        return self._inner.phases()

    def add(self, tasks: list[Task], skip_duplicates: bool = True) -> int:
        return self._inner.add(tasks, skip_duplicates=skip_duplicates)

    def claim(self, task_id: str, runner_id: str) -> Task:
        return self._inner.claim(task_id, runner_id)

    def submit_result(self, task_id: str, result: TaskResult) -> Task:
        return self._inner.submit_result(task_id, result)

    def checkpoint_result(self, task_id: str, result: TaskResult) -> Task:
        return self._inner.checkpoint_result(task_id, result)

    def mark_done(self, task_id: str, result: TaskResult) -> Task:
        return self._inner.mark_done(task_id, result)

    def mark_failed(self, task_id: str, error: str) -> Task:
        return self._inner.mark_failed(task_id, error)

    def skip(self, task_id: str, reason: str = "") -> Task:
        return self._inner.skip(task_id, reason=reason)

    def pause(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Task:
        return self._inner.pause(task_id, reason=reason, payload=payload)

    def resume(self, task_id: str, runner_id: str) -> Task:
        return self._inner.resume(task_id, runner_id)

    def reset_in_progress(self, runner_id: str | None = None) -> int:
        return self._inner.reset_in_progress(runner_id=runner_id)


def test_runner_accepts_runtime_store_protocol(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    progress_path = tmp_path / "progress.md"
    inner = TaskLedger(path=ledger_path, progress_file=str(progress_path))
    store = ForwardingRuntimeStore(inner)

    task = Task(
        title="protocol smoke",
        description="verify runner decoupling from TaskLedger concrete type",
        verifier_id="schema",
        verifier_config={"required_fields": ["answer"]},
    )
    store.add([task])

    config = VeridianConfig(
        dry_run=True,
        ledger_file=ledger_path,
        progress_file=progress_path,
    )
    summary = VeridianRunner(
        ledger=store,
        provider=MockProvider(),
        config=config,
    ).run()

    assert summary.total_tasks == 1
    updated = store.get(task.id)
    assert updated.status == TaskStatus.SKIPPED
