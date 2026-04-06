"""
RuntimeStoreBridge adapts BaseStorage backends to the RuntimeStore contract.
"""

from __future__ import annotations

import builtins
import logging
from datetime import UTC, datetime
from typing import Any

from veridian.core.exceptions import (
    InvalidTransition,
    TaskAlreadyClaimed,
    TaskNotFound,
    TaskNotPaused,
)
from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus
from veridian.storage.base import BaseStorage

__all__ = ["RuntimeStoreBridge"]

log = logging.getLogger(__name__)


class RuntimeStoreBridge:
    """Adapter from BaseStorage to the full RuntimeStore runner protocol."""

    def __init__(self, backend: BaseStorage) -> None:
        self._backend = backend

    # Read operations
    def get(self, task_id: str) -> Task:
        return self._backend.get(task_id)

    def get_next(
        self,
        phase: str | None = None,
        respect_dependencies: bool = True,
        include_paused: bool = False,
    ) -> Task | None:
        all_tasks = self._backend.list_all()
        done_ids = {t.id for t in all_tasks if t.status == TaskStatus.DONE}

        if include_paused:
            paused = [
                t
                for t in all_tasks
                if t.status == TaskStatus.PAUSED and (phase is None or t.phase == phase)
            ]
            if paused:
                paused.sort(key=lambda t: (-t.priority, t.created_at))
                return paused[0]

        candidates = [
            t
            for t in all_tasks
            if t.status == TaskStatus.PENDING
            and (phase is None or t.phase == phase)
            and (not respect_dependencies or all(dep in done_ids for dep in t.depends_on))
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: (-t.priority, t.created_at))
        return candidates[0]

    def list(
        self,
        status: TaskStatus | str | None = None,
        phase: str | None = None,
        priority_gte: int | None = None,
    ) -> builtins.list[Task]:
        tasks = self._backend.list_all()
        if status is not None:
            status_value = status.value if isinstance(status, TaskStatus) else status
            tasks = [t for t in tasks if t.status.value == status_value]
        if phase is not None:
            tasks = [t for t in tasks if t.phase == phase]
        if priority_gte is not None:
            tasks = [t for t in tasks if t.priority >= priority_gte]
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks

    def stats(self) -> LedgerStats:
        return self._backend.stats()

    def phases(self) -> builtins.list[str]:
        tasks = self._backend.list_all()
        tasks.sort(key=lambda t: -t.priority)
        seen: builtins.list[str] = []
        for task in tasks:
            if task.phase not in seen:
                seen.append(task.phase)
        return seen

    # Write operations
    def add(self, tasks: builtins.list[Task], skip_duplicates: bool = True) -> int:
        added = 0
        for task in tasks:
            if skip_duplicates:
                try:
                    self._backend.get(task.id)
                    continue
                except TaskNotFound:
                    pass
            self._backend.put(task)
            added += 1
        return added

    def claim(self, task_id: str, runner_id: str) -> Task:
        task = self._backend.get(task_id)
        if task.status == TaskStatus.IN_PROGRESS:
            if task.claimed_by and task.claimed_by != runner_id:
                raise TaskAlreadyClaimed(
                    f"Task {task_id} is already claimed by {task.claimed_by!r}"
                )
            return task
        self._transition(task, TaskStatus.IN_PROGRESS)
        task.claimed_by = runner_id
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def submit_result(self, task_id: str, result: TaskResult) -> Task:
        task = self._backend.get(task_id)
        self._transition(task, TaskStatus.VERIFYING)
        task.result = result
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def checkpoint_result(self, task_id: str, result: TaskResult) -> Task:
        task = self._backend.get(task_id)
        task.result = result
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def mark_done(self, task_id: str, result: TaskResult) -> Task:
        task = self._backend.get(task_id)
        self._transition(task, TaskStatus.DONE)
        result.verified = True
        result.verified_at = datetime.now(tz=UTC)
        task.result = result
        task.claimed_by = None
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def mark_failed(self, task_id: str, error: str) -> Task:
        task = self._backend.get(task_id)
        task.retry_count += 1
        task.last_error = error
        task.claimed_by = None
        task.updated_at = datetime.now(tz=UTC)

        self._transition(task, TaskStatus.FAILED)
        if task.retry_count > task.max_retries:
            self._transition(task, TaskStatus.ABANDONED)
            log.warning("task.abandoned id=%s retries=%d", task_id, task.retry_count)

        self._backend.put(task)
        return task

    def skip(self, task_id: str, reason: str = "") -> Task:
        task = self._backend.get(task_id)
        self._transition(task, TaskStatus.SKIPPED)
        task.last_error = reason
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def pause(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Task:
        task = self._backend.get(task_id)
        self._transition(task, TaskStatus.PAUSED)

        result = task.result if task.result is not None else TaskResult(raw_output="")
        existing_pause: dict[str, Any] = result.extras.get("pause_payload") or {}
        pause_payload: dict[str, Any] = {
            "reason": reason,
            "cursor": (payload or {}).get("cursor", existing_pause.get("cursor")),
            "resume_hint": (payload or {}).get("resume_hint") or existing_pause.get("resume_hint"),
            "paused_at": datetime.now(tz=UTC).isoformat(),
            "resume_count": int(existing_pause.get("resume_count", 0)),
        }
        for key, value in (payload or {}).items():
            if key not in {"cursor", "resume_hint"}:
                pause_payload.setdefault(key, value)
        result.extras["pause_payload"] = pause_payload
        task.result = result
        task.claimed_by = None
        task.updated_at = datetime.now(tz=UTC)
        self._backend.put(task)
        return task

    def resume(self, task_id: str, runner_id: str) -> Task:
        task = self._backend.get(task_id)
        if task.status != TaskStatus.PAUSED:
            raise TaskNotPaused(task_id=task_id, status=task.status.value)
        self._transition(task, TaskStatus.IN_PROGRESS)
        task.claimed_by = runner_id
        task.updated_at = datetime.now(tz=UTC)

        if task.result is not None:
            pause_payload: dict[str, Any] = task.result.extras.get("pause_payload") or {}
            pause_payload["resume_count"] = int(pause_payload.get("resume_count", 0)) + 1
            pause_payload["resumed_at"] = datetime.now(tz=UTC).isoformat()
            task.result.extras["pause_payload"] = pause_payload

        self._backend.put(task)
        return task

    def reset_in_progress(self, runner_id: str | None = None) -> int:
        reset = 0
        for task in self._backend.list_all():
            if task.status != TaskStatus.IN_PROGRESS:
                continue
            if runner_id is not None and task.claimed_by != runner_id:
                continue
            task.status = TaskStatus.PENDING
            task.claimed_by = None
            task.updated_at = datetime.now(tz=UTC)
            self._backend.put(task)
            reset += 1
        return reset

    @staticmethod
    def _transition(task: Task, new_status: TaskStatus) -> None:
        if not task.can_transition_to(new_status):
            raise InvalidTransition(
                f"Cannot transition task {task.id!r} "
                f"from {task.status.value!r} to {new_status.value!r}"
            )
        task.status = new_status
