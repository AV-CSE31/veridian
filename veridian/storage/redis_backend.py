"""
veridian.storage.redis_backend
────────────────────────────────
RedisStorage — Redis-backed task storage with sorted-set priority queue.

Rules:
- Requires the `redis` optional extra: ``pip install veridian-ai[redis]``.
- get_next(): sorted set keyed by priority, SETNX for distributed lock.
- All task data stored as JSON hash in Redis.
- Import guard: raises ImportError with pip hint if redis is not installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from veridian.core.exceptions import StorageLockError, TaskNotFound
from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus
from veridian.integrations.tenancy import TenantIsolationError
from veridian.storage.base import BaseStorage

log = logging.getLogger(__name__)

__all__ = ["RedisStorage"]

# Key prefixes
_TASK_KEY = "veridian:task:{task_id}"
_QUEUE_KEY = "veridian:queue"  # sorted set; score = priority (higher = first)
_LOCK_KEY = "veridian:lock:get_next"
_LOCK_TTL = 5_000  # milliseconds


class RedisStorage(BaseStorage):
    """
    Redis-backed task storage.

    Uses a sorted set (ZREVRANGE by score) as the priority queue.
    SETNX (SET NX PX) provides a distributed lock for get_next().

    Requires: ``pip install veridian-ai[redis]``
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        ssl: bool = False,
        key_prefix: str = "",
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as exc:
            raise ImportError(
                "redis is required for RedisStorage. "
                "Install it with: pip install veridian-ai[redis]"
            ) from exc

        self._r = redis_lib.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            ssl=ssl,
            decode_responses=True,
        )
        self._prefix = key_prefix

    def _task_key(self, task_id: str) -> str:
        return f"{self._prefix}{_TASK_KEY.format(task_id=task_id)}"

    def _queue_key(self) -> str:
        return f"{self._prefix}{_QUEUE_KEY}"

    def _lock_key(self) -> str:
        return f"{self._prefix}{_LOCK_KEY}"

    # ── BaseStorage interface ─────────────────────────────────────────────────

    def put(self, task: Task) -> None:
        """Upsert a task. Adds/updates the sorted-set entry if PENDING."""
        key = self._task_key(task.id)
        self._r.set(key, json.dumps(task.to_dict()))
        if task.status == TaskStatus.PENDING:
            self._r.zadd(self._queue_key(), {task.id: task.priority})
        else:
            # Remove from queue if no longer pending
            self._r.zrem(self._queue_key(), task.id)

    def get(self, task_id: str, *, tenant_id: str | None = None) -> Task:
        """Retrieve a task by ID. Raises TaskNotFound if missing.

        When *tenant_id* is set, the task must belong to that tenant.
        """
        raw = self._r.get(self._task_key(task_id))
        if raw is None:
            raise TaskNotFound(f"Task '{task_id}' not found in Redis.")
        task = Task.from_dict(json.loads(cast(str, raw)))
        if tenant_id is not None and not task.id.startswith(f"{tenant_id}::"):
            raise TenantIsolationError(f"Task {task_id!r} does not belong to tenant {tenant_id!r}")
        return task

    def get_next(self) -> Task | None:
        """
        Pop the highest-priority PENDING task from the sorted set.
        Uses SETNX for a distributed lock to prevent double-claiming.
        """
        lock_key = self._lock_key()
        acquired = self._r.set(lock_key, "1", nx=True, px=_LOCK_TTL)
        if not acquired:
            raise StorageLockError(
                "Could not acquire Redis lock for get_next(). Another process may be holding it."
            )
        try:
            done_set = self._get_done_ids()
            # Iterate from highest priority downward
            candidates: list[Any] = cast(list[Any], self._r.zrevrange(self._queue_key(), 0, -1))
            for task_id in candidates:
                raw = self._r.get(self._task_key(task_id))
                if raw is None:
                    continue
                task_dict: dict[str, Any] = json.loads(cast(str, raw))
                if task_dict.get("status") != TaskStatus.PENDING.value:
                    self._r.zrem(self._queue_key(), task_id)
                    continue
                deps = task_dict.get("depends_on", [])
                if not all(dep in done_set for dep in deps):
                    continue
                # Claim it
                task = Task.from_dict(task_dict)
                task.status = TaskStatus.IN_PROGRESS
                self._r.set(self._task_key(task.id), json.dumps(task.to_dict()))
                self._r.zrem(self._queue_key(), task.id)
                return task
            return None
        finally:
            self._r.delete(lock_key)

    def complete(self, task_id: str, result: TaskResult) -> None:
        """Mark a task as DONE."""
        raw = self._r.get(self._task_key(task_id))
        if raw is None:
            raise TaskNotFound(f"Task '{task_id}' not found in Redis.")
        task = Task.from_dict(json.loads(cast(str, raw)))
        task.status = TaskStatus.DONE
        task.result = result
        self._r.set(self._task_key(task_id), json.dumps(task.to_dict()))
        self._r.zrem(self._queue_key(), task_id)

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as FAILED."""
        raw = self._r.get(self._task_key(task_id))
        if raw is None:
            raise TaskNotFound(f"Task '{task_id}' not found in Redis.")
        task = Task.from_dict(json.loads(cast(str, raw)))
        task.status = TaskStatus.FAILED
        task.last_error = error
        self._r.set(self._task_key(task_id), json.dumps(task.to_dict()))
        self._r.zrem(self._queue_key(), task_id)

    def list_all(self, *, tenant_id: str | None = None) -> list[Task]:
        """Return all tasks stored in Redis (scan-based, may be slow on large datasets).

        When *tenant_id* is set, only tasks belonging to that tenant are returned.
        """
        pattern = f"{self._prefix}veridian:task:*"
        keys = list(self._r.scan_iter(pattern))
        tasks: list[Task] = []
        prefix = f"{tenant_id}::" if tenant_id is not None else None
        for key in keys:
            raw = self._r.get(key)
            if raw:
                task = Task.from_dict(json.loads(cast(str, raw)))
                if prefix is not None and not task.id.startswith(prefix):
                    continue
                tasks.append(task)
        return tasks

    def stats(self) -> LedgerStats:
        """Return aggregate statistics."""
        tasks = self.list_all()
        by_status: dict[str, int] = {}
        for task in tasks:
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
        return LedgerStats(total=len(tasks), by_status=by_status)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_done_ids(self) -> set[str]:
        """Scan all tasks and return the IDs of those with status DONE."""
        return {t.id for t in self.list_all() if t.status == TaskStatus.DONE}
