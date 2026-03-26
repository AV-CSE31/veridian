"""
veridian.storage.postgres_backend
───────────────────────────────────
PostgresStorage — PostgreSQL-backed task storage.

Rules:
- Requires the `postgres` optional extra: ``pip install veridian-ai[postgres]``.
- get_next(): SELECT FOR UPDATE SKIP LOCKED for concurrent worker safety.
- Auto-migrate on __init__() — creates table if it doesn't exist.
- Uses psycopg2 (sync) for simplicity; asyncpg is available for async callers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from veridian.core.exceptions import StorageConnectionError, TaskNotFound
from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus
from veridian.storage.base import BaseStorage

log = logging.getLogger(__name__)

__all__ = ["PostgresStorage"]

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS veridian_tasks (
    id          TEXT PRIMARY KEY,
    priority    INTEGER NOT NULL DEFAULT 50,
    status      TEXT NOT NULL DEFAULT 'pending',
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_veridian_tasks_status_priority
    ON veridian_tasks (status, priority DESC);
"""


class PostgresStorage(BaseStorage):
    """
    PostgreSQL-backed task storage with advisory locking.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` to allow multiple workers
    to safely pull tasks from the queue without collision.

    Auto-migrates on construction (creates the table if absent).

    Requires: ``pip install veridian-ai[postgres]``
    """

    def __init__(
        self,
        dsn: str,
        table: str = "veridian_tasks",
    ) -> None:
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for PostgresStorage. "
                "Install it with: pip install veridian-ai[postgres]"
            ) from exc

        self._dsn = dsn
        self._table = table
        self._psycopg2 = psycopg2

        # Auto-migrate
        self._migrate()

    def _connect(self) -> Any:
        try:
            return self._psycopg2.connect(self._dsn)
        except Exception as exc:
            raise StorageConnectionError(
                f"Cannot connect to PostgreSQL at '{self._dsn}': {exc}"
            ) from exc

    def _migrate(self) -> None:
        """Create the tasks table and index if they don't exist."""
        # Substitute the table name safely (not user input in production)
        sql = _CREATE_TABLE_SQL.replace("veridian_tasks", self._table)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    # ── BaseStorage interface ─────────────────────────────────────────────────

    def put(self, task: Task) -> None:
        """Insert or update a task (upsert by ID)."""
        data = json.dumps(task.to_dict())
        sql = f"""
            INSERT INTO {self._table} (id, priority, status, data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET priority   = EXCLUDED.priority,
                    status     = EXCLUDED.status,
                    data       = EXCLUDED.data,
                    updated_at = now()
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (task.id, task.priority, task.status.value, data))
            conn.commit()

    def get(self, task_id: str) -> Task:
        """Retrieve a task by ID. Raises TaskNotFound if missing."""
        sql = f"SELECT data FROM {self._table} WHERE id = %s"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (task_id,))
            row = cur.fetchone()
        if row is None:
            raise TaskNotFound(f"Task '{task_id}' not found in PostgreSQL.")
        return Task.from_dict(json.loads(row[0]))

    def get_next(self) -> Task | None:
        """
        Return and atomically claim the highest-priority PENDING task
        whose dependencies are all DONE, using SELECT FOR UPDATE SKIP LOCKED.
        """
        # First collect DONE IDs (needed for dependency check)
        done_sql = f"SELECT id FROM {self._table} WHERE status = 'done'"
        # Main queue query
        queue_sql = f"""
            SELECT id, data FROM {self._table}
            WHERE status = 'pending'
            ORDER BY priority DESC
            FOR UPDATE SKIP LOCKED
            LIMIT 50
        """
        update_sql = f"""
            UPDATE {self._table}
            SET status = 'in_progress', updated_at = now()
            WHERE id = %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(done_sql)
                done_ids = {row[0] for row in cur.fetchall()}

                cur.execute(queue_sql)
                rows = cur.fetchall()

            for _task_id, raw_data in rows:
                task_dict: dict[str, Any] = json.loads(raw_data)
                deps = task_dict.get("depends_on", [])
                if not all(dep in done_ids for dep in deps):
                    continue
                # Claim it
                task = Task.from_dict(task_dict)
                task.status = TaskStatus.IN_PROGRESS
                with conn.cursor() as cur:
                    cur.execute(update_sql, (task.id,))
                conn.commit()
                return task
        return None

    def complete(self, task_id: str, result: TaskResult) -> None:
        """Mark a task as DONE."""
        task = self.get(task_id)
        task.status = TaskStatus.DONE
        task.result = result
        self.put(task)

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as FAILED."""
        task = self.get(task_id)
        task.status = TaskStatus.FAILED
        task.last_error = error
        self.put(task)

    def list_all(self) -> list[Task]:
        """Return all tasks in the table."""
        sql = f"SELECT data FROM {self._table}"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return [Task.from_dict(json.loads(r[0])) for r in rows]

    def stats(self) -> LedgerStats:
        """Return aggregate statistics."""
        sql = f"SELECT status, COUNT(*) FROM {self._table} GROUP BY status"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        by_status = {status: count for status, count in rows}
        return LedgerStats(total=sum(by_status.values()), by_status=by_status)
