"""
veridian.storage.local_json
────────────────────────────
LocalJSONStorage — file-backed task storage.

Rules:
- Zero external deps beyond stdlib + filelock (already a core dep).
- All writes atomic: temp file → os.replace().
- FileLock ensures single writer across processes.
- get_next() returns highest-priority PENDING task with all deps DONE.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

from veridian.core.exceptions import TaskNotFound
from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus
from veridian.storage.base import BaseStorage

log = logging.getLogger(__name__)

__all__ = ["LocalJSONStorage"]

_SCHEMA_VERSION = 1


class LocalJSONStorage(BaseStorage):
    """
    File-backed task storage using a single JSON file.

    Thread-safe via FileLock. All writes are atomic (temp → os.replace).
    Zero external dependencies beyond stdlib and filelock.
    """

    def __init__(self, storage_file: Path) -> None:
        self._file = storage_file
        self._lock_file = Path(str(storage_file) + ".lock")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _lock(self) -> FileLock:
        return FileLock(str(self._lock_file))

    def _load_raw(self) -> dict[str, Any]:
        """Load the raw JSON data from disk."""
        if not self._file.exists():
            return {"schema_version": _SCHEMA_VERSION, "tasks": {}}
        try:
            result: dict[str, Any] = json.loads(self._file.read_text(encoding="utf-8"))
            return result
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read storage file %s: %s", self._file, exc)
            return {"schema_version": _SCHEMA_VERSION, "tasks": {}}

    def _save_raw(self, data: dict[str, Any]) -> None:
        """Atomically write JSON data to disk."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        fd, tmp = tempfile.mkstemp(
            dir=self._file.parent,
            prefix=".storage_",
            suffix=".tmp",
        )
        try:
            os.write(fd, content)
            os.close(fd)
            os.replace(tmp, self._file)
        except Exception:
            with __import__("contextlib").suppress(OSError):
                os.close(fd)
            with __import__("contextlib").suppress(OSError):
                os.unlink(tmp)
            raise

    def _task_map(self, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return the tasks dict from raw data, normalising list→dict if needed."""
        raw: Any = data.get("tasks", {})
        if isinstance(raw, list):
            # Migrate old list format
            return {t["id"]: t for t in raw}
        result: dict[str, dict[str, Any]] = raw
        return result

    # ── BaseStorage interface ─────────────────────────────────────────────────

    def put(self, task: Task) -> None:
        """Insert or update a task."""
        with self._lock():
            data = self._load_raw()
            tasks = self._task_map(data)
            tasks[task.id] = task.to_dict()
            data["tasks"] = tasks
            self._save_raw(data)

    def get(self, task_id: str) -> Task:
        """Retrieve a task by ID. Raises TaskNotFound if missing."""
        with self._lock():
            data = self._load_raw()
            tasks = self._task_map(data)
            if task_id not in tasks:
                raise TaskNotFound(f"Task '{task_id}' not found in storage.")
            return Task.from_dict(tasks[task_id])

    def get_next(self) -> Task | None:
        """
        Return and claim the highest-priority PENDING task whose deps are all DONE.
        Returns None if no eligible task exists.
        """
        with self._lock():
            data = self._load_raw()
            tasks = self._task_map(data)
            done_ids = {tid for tid, t in tasks.items() if t.get("status") == TaskStatus.DONE.value}
            candidates = [
                Task.from_dict(t)
                for t in tasks.values()
                if t.get("status") == TaskStatus.PENDING.value
                and all(dep in done_ids for dep in t.get("depends_on", []))
            ]
            if not candidates:
                return None
            # Highest priority first; break ties by insertion order (stable sort)
            candidates.sort(key=lambda t: -t.priority)
            best = candidates[0]
            # Claim it
            best.status = TaskStatus.IN_PROGRESS
            tasks[best.id] = best.to_dict()
            data["tasks"] = tasks
            self._save_raw(data)
            return best

    def complete(self, task_id: str, result: TaskResult) -> None:
        """Mark a task as DONE with the given result."""
        with self._lock():
            data = self._load_raw()
            tasks = self._task_map(data)
            if task_id not in tasks:
                raise TaskNotFound(f"Task '{task_id}' not found in storage.")
            task = Task.from_dict(tasks[task_id])
            task.status = TaskStatus.DONE
            task.result = result
            tasks[task_id] = task.to_dict()
            data["tasks"] = tasks
            self._save_raw(data)

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as FAILED with the given error message."""
        with self._lock():
            data = self._load_raw()
            tasks = self._task_map(data)
            if task_id not in tasks:
                raise TaskNotFound(f"Task '{task_id}' not found in storage.")
            task = Task.from_dict(tasks[task_id])
            task.status = TaskStatus.FAILED
            task.last_error = error
            tasks[task_id] = task.to_dict()
            data["tasks"] = tasks
            self._save_raw(data)

    def list_all(self) -> list[Task]:
        """Return all tasks in storage."""
        with self._lock():
            data = self._load_raw()
            return [Task.from_dict(t) for t in self._task_map(data).values()]

    def stats(self) -> LedgerStats:
        """Return aggregate statistics over all tasks."""
        tasks = self.list_all()
        by_status: dict[str, int] = {}
        for task in tasks:
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
        return LedgerStats(
            total=len(tasks),
            by_status=by_status,
        )
