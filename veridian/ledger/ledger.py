"""
veridian.ledger.ledger
─────────────────────
TaskLedger — the single source of truth for all task state.

RULES:
- Ledger is the ONLY object allowed to transition task status.
- All writes are atomic (temp-file → rename via os.replace).
- FileLock ensures single writer across processes.
- reset_in_progress() MUST be called at the start of every run().
"""

from __future__ import annotations

import builtins
import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from veridian.core.exceptions import (
    InvalidTransition,
    LedgerCorrupted,
    TaskAlreadyClaimed,
    TaskNotFound,
    TaskNotPaused,
)
from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


class TaskLedger:
    """
    Thread-safe, crash-safe task ledger backed by a JSON file.

    Usage::

        ledger = TaskLedger("ledger.json")
        ledger.add([Task(title="do something", ...)])
        task = ledger.get_next()
        ledger.claim(task.id, run_id="my-run-001")
        ...
        ledger.mark_done(task.id, result)
    """

    def __init__(
        self,
        path: str | Path = "ledger.json",
        run_id: str | None = None,
        progress_file: str = "progress.md",
        lock_timeout: float = 15.0,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.progress_path = Path(progress_file)
        self._lock_path = self.path.with_suffix(".lock")
        self._lock = FileLock(str(self._lock_path), timeout=lock_timeout)

        # Initialise empty ledger if file doesn't exist
        if not self.path.exists():
            self._write_raw({"schema_version": SCHEMA_VERSION, "tasks": {}})

    # ── READ INTERFACE ────────────────────────────────────────────────────────

    def get(self, task_id: str) -> Task:
        """Return a copy of the task. Raises TaskNotFound if missing."""
        data = self._read_raw()
        if task_id not in data["tasks"]:
            raise TaskNotFound(f"Task {task_id!r} not found in ledger")
        return Task.from_dict(data["tasks"][task_id])

    def get_next(
        self,
        phase: str | None = None,
        respect_dependencies: bool = True,
        include_paused: bool = False,
    ) -> Task | None:
        """
        Return the highest-priority schedulable task. Returns None when empty.

        Normal mode: returns a PENDING task whose dependencies are all DONE.

        include_paused=True (RV3-001): also considers PAUSED tasks and prefers
        them over PENDING work so HITL approvals aren't starved. Dependency
        gating does not apply to resumes because the task was already running.
        """
        data = self._read_raw()
        tasks = [Task.from_dict(t) for t in data["tasks"].values()]
        done_ids = {t.id for t in tasks if t.status == TaskStatus.DONE}

        # RV3-001: Resume-first policy — surface PAUSED tasks before PENDING ones.
        if include_paused:
            paused = [
                t
                for t in tasks
                if t.status == TaskStatus.PAUSED and (phase is None or t.phase == phase)
            ]
            if paused:
                paused.sort(key=lambda t: (-t.priority, t.created_at))
                return paused[0]

        candidates = [
            t
            for t in tasks
            if t.status == TaskStatus.PENDING
            and (phase is None or t.phase == phase)
            and (not respect_dependencies or all(dep in done_ids for dep in t.depends_on))
        ]

        if not candidates:
            return None

        # Sort: priority DESC, created_at ASC (FIFO within same priority)
        candidates.sort(key=lambda t: (-t.priority, t.created_at))
        return candidates[0]

    def list(
        self,
        status: TaskStatus | str | None = None,
        phase: str | None = None,
        priority_gte: int | None = None,
    ) -> builtins.list[Task]:
        """Return filtered list of tasks. Returns copies."""
        data = self._read_raw()
        tasks = [Task.from_dict(t) for t in data["tasks"].values()]

        if status is not None:
            sv = status.value if isinstance(status, TaskStatus) else status
            tasks = [t for t in tasks if t.status.value == sv]
        if phase is not None:
            tasks = [t for t in tasks if t.phase == phase]
        if priority_gte is not None:
            tasks = [t for t in tasks if t.priority >= priority_gte]

        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks

    def stats(self) -> LedgerStats:
        """Compute current ledger statistics."""
        data = self._read_raw()
        tasks = [Task.from_dict(t) for t in data["tasks"].values()]

        by_status: dict[str, int] = {}
        phases: dict[str, int] = {}
        total_tokens = 0
        total_retries = 0

        for t in tasks:
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
            if t.status == TaskStatus.PENDING:
                phases[t.phase] = phases.get(t.phase, 0) + 1
            total_retries += t.retry_count
            if t.result:
                total_tokens += t.result.token_usage.get("total_tokens", 0)

        n = len(tasks)
        return LedgerStats(
            total=n,
            by_status=by_status,
            phases=phases,
            retry_rate=total_retries / max(n, 1),
            total_tokens_used=total_tokens,
        )

    def phases(self) -> builtins.list[str]:
        """Return distinct phase names, ordered by first-seen task priority."""
        data = self._read_raw()
        tasks = sorted(
            [Task.from_dict(t) for t in data["tasks"].values()],
            key=lambda t: -t.priority,
        )
        seen: list[str] = []
        for t in tasks:
            if t.phase not in seen:
                seen.append(t.phase)
        return seen

    # ── WRITE INTERFACE ───────────────────────────────────────────────────────

    def add(self, tasks: builtins.list[Task], skip_duplicates: bool = True) -> int:
        """
        Add tasks to the ledger. Returns count added.
        If skip_duplicates=True and a task with the same id exists, skip it.
        """
        added = 0
        with self._lock:
            data = self._read_raw()
            for task in tasks:
                if task.id in data["tasks"] and skip_duplicates:
                    continue
                data["tasks"][task.id] = task.to_dict()
                added += 1
            self._write_raw(data)
        log.debug("ledger.add count=%d skip_dup=%s", added, skip_duplicates)
        return added

    def claim(self, task_id: str, runner_id: str) -> Task:
        """
        Transition PENDING → IN_PROGRESS. Idempotent for the same runner.
        Raises TaskAlreadyClaimed if another runner holds it.
        Returns the updated task.
        """
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])

            if task.status == TaskStatus.IN_PROGRESS:
                if task.claimed_by and task.claimed_by != runner_id:
                    raise TaskAlreadyClaimed(
                        f"Task {task_id} is already claimed by {task.claimed_by!r}"
                    )
                # Same runner re-claiming an already IN_PROGRESS task — idempotent
                return task

            self._transition(task, TaskStatus.IN_PROGRESS)
            task.claimed_by = runner_id
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)

        return task

    def submit_result(self, task_id: str, result: TaskResult) -> Task:
        """IN_PROGRESS → VERIFYING. Does NOT mark DONE — verifier does that."""
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.VERIFYING)
            task.result = result
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        return task

    def checkpoint_result(self, task_id: str, result: TaskResult) -> Task:
        """
        Persist intermediate task evidence without changing lifecycle status.

        This is used by replay-aware runners to save deterministic checkpoints
        (trace, score boundaries, policy logs, invocation IDs) after each step.
        """
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            task.result = result
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        return task

    def mark_done(self, task_id: str, result: TaskResult) -> Task:
        """VERIFYING → DONE. Called ONLY by VeridianRunner after verifier passes."""
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.DONE)
            result.verified = True
            result.verified_at = datetime.now(tz=UTC)
            task.result = result
            task.claimed_by = None
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        self.log(f"[DONE] {task_id} — {task.title[:60]}")
        return task

    def mark_failed(self, task_id: str, error: str) -> Task:
        """
        → FAILED. Auto-transitions to ABANDONED if retry_count > max_retries.
        Increments retry_count. Stores error as last_error (for next prompt).
        ABANDONED path: IN_PROGRESS → FAILED → ABANDONED (respects state machine).
        """
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            task.retry_count += 1
            task.last_error = error
            task.claimed_by = None
            task.updated_at = datetime.now(tz=UTC)

            self._transition(task, TaskStatus.FAILED)

            if task.retry_count > task.max_retries:
                # Two-step: FAILED → ABANDONED (state machine compliant)
                self._transition(task, TaskStatus.ABANDONED)
                log.warning("task.abandoned id=%s retries=%d", task_id, task.retry_count)

            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        return task

    def skip(self, task_id: str, reason: str = "") -> Task:
        """→ SKIPPED. Terminal. Use for human-curated exclusions."""
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.SKIPPED)
            task.last_error = reason
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        return task

    def pause(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Task:
        """
        RV3-001: IN_PROGRESS → PAUSED. Persists pause metadata in
        ``task.result.extras['pause_payload']`` so resume() can restore context.

        The pause payload carries the reason, an optional worker cursor, and a
        resume_count that increments on each resume. Crash-safe via atomic write.
        """
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.PAUSED)

            # Preserve any existing TaskResult (e.g. from checkpoint_result())
            # and append/refresh the pause_payload extras entry.
            result = task.result if task.result is not None else TaskResult(raw_output="")
            existing_pause = result.extras.get("pause_payload") or {}
            pause_payload: dict[str, Any] = {
                "reason": reason,
                "cursor": (payload or {}).get("cursor", existing_pause.get("cursor")),
                "resume_hint": (payload or {}).get("resume_hint")
                or existing_pause.get("resume_hint"),
                "paused_at": datetime.now(tz=UTC).isoformat(),
                "resume_count": int(existing_pause.get("resume_count", 0)),
            }
            # Allow arbitrary extra keys from the caller payload without
            # letting them clobber the canonical fields above.
            for key, value in (payload or {}).items():
                if key not in {"cursor", "resume_hint"}:
                    pause_payload.setdefault(key, value)
            result.extras["pause_payload"] = pause_payload
            task.result = result
            task.claimed_by = None
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        log.info("ledger.pause task_id=%s reason=%s", task_id, reason[:60])
        self.log(f"[PAUSE] {task_id} — {reason[:80]}")
        return task

    def resume(self, task_id: str, runner_id: str) -> Task:
        """
        RV3-001: PAUSED → IN_PROGRESS. Increments resume_count, sets claimed_by.
        Raises TaskNotPaused if the task is not in PAUSED state.
        """
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            if task.status != TaskStatus.PAUSED:
                raise TaskNotPaused(task_id=task_id, status=task.status.value)

            self._transition(task, TaskStatus.IN_PROGRESS)
            task.claimed_by = runner_id
            task.updated_at = datetime.now(tz=UTC)

            if task.result is not None:
                pause_payload = task.result.extras.get("pause_payload") or {}
                pause_payload["resume_count"] = int(pause_payload.get("resume_count", 0)) + 1
                pause_payload["resumed_at"] = datetime.now(tz=UTC).isoformat()
                task.result.extras["pause_payload"] = pause_payload

            data["tasks"][task_id] = task.to_dict()
            self._write_raw(data)
        log.info("ledger.resume task_id=%s runner_id=%s", task_id, runner_id)
        self.log(f"[RESUME] {task_id}")
        return task

    def reset_in_progress(self, runner_id: str | None = None) -> int:
        """
        CRITICAL: Call this at the start of EVERY run().
        Resets IN_PROGRESS tasks back to PENDING (crash recovery).
        If runner_id given: only reset tasks claimed by that runner.
        Returns count reset.

        RV3-001 guarantee: PAUSED tasks are NEVER reset. Their pause payload is
        preserved so they can be resumed on next run().
        """
        reset = 0
        with self._lock:
            data = self._read_raw()
            for task_dict in data["tasks"].values():
                if task_dict.get("status") != "in_progress":
                    continue
                if runner_id and task_dict.get("claimed_by") != runner_id:
                    continue
                task_dict["status"] = "pending"
                task_dict["claimed_by"] = None
                task_dict["updated_at"] = datetime.now(tz=UTC).isoformat()
                reset += 1
            if reset:
                self._write_raw(data)

        if reset:
            log.info("ledger.reset_in_progress count=%d", reset)
            self.log(f"[RESET] {reset} stale IN_PROGRESS tasks → PENDING (crash recovery)")
        return reset

    def reset_failed(self, task_ids: builtins.list[str] | None = None) -> int:
        """
        Reset FAILED/ABANDONED tasks → PENDING for re-queue.
        retry_count is preserved so the abandonment threshold remains accurate
        across multiple reset cycles.
        """
        reset = 0
        with self._lock:
            data = self._read_raw()
            for tid, task_dict in data["tasks"].items():
                if task_ids and tid not in task_ids:
                    continue
                if task_dict.get("status") not in {"failed", "abandoned"}:
                    continue
                task_dict["status"] = "pending"
                task_dict["last_error"] = None
                task_dict["updated_at"] = datetime.now(tz=UTC).isoformat()
                reset += 1
            if reset:
                self._write_raw(data)
        return reset

    # ── PROGRESS LOG ──────────────────────────────────────────────────────────

    def log(self, message: str, level: str = "INFO") -> None:
        """
        Append a timestamped entry to progress.md.
        Agents read this on startup for fast orientation.
        """
        ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}\n"
        with open(self.progress_path, "a", encoding="utf-8") as f:
            f.write(line)

    def read_recent_log(self, n: int = 10) -> builtins.list[str]:
        """Return the last n lines of progress.md."""
        if not self.progress_path.exists():
            return []
        lines = self.progress_path.read_text(encoding="utf-8").splitlines()
        return lines[-n:]

    # ── INTERNAL ──────────────────────────────────────────────────────────────

    def _read_raw(self) -> dict[str, Any]:
        """Read and parse ledger.json. Raises LedgerCorrupted on parse failure."""
        try:
            text = self.path.read_text(encoding="utf-8")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise LedgerCorrupted("ledger.json root must be an object")
            return self._normalize_legacy_shape(dict(payload))
        except json.JSONDecodeError as e:
            raise LedgerCorrupted(f"ledger.json is malformed: {e}") from e
        except FileNotFoundError:
            return {"schema_version": SCHEMA_VERSION, "tasks": {}}

    @staticmethod
    def _normalize_legacy_shape(data: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize historical ledger shapes to the canonical in-memory format.

        Legacy CLI versions wrote ``{"tasks": []}`` instead of ``{"tasks": {}}``.
        This method keeps reads backward-compatible by coercing task containers
        to a task-id keyed dict.
        """
        raw_tasks = data.get("tasks", {})
        tasks: dict[str, Any] = {}
        if isinstance(raw_tasks, dict):
            tasks = raw_tasks
        elif isinstance(raw_tasks, list):
            for item in raw_tasks:
                if not isinstance(item, dict):
                    continue
                task_id = item.get("id")
                if isinstance(task_id, str) and task_id:
                    tasks[task_id] = item

        data["tasks"] = tasks
        if not isinstance(data.get("schema_version"), int):
            data["schema_version"] = SCHEMA_VERSION
        return data

    def _write_raw(self, data: dict[str, Any]) -> None:
        """
        Atomic write via temp file + os.replace().
        Validates JSON round-trip before renaming.
        """
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = datetime.now(tz=UTC).isoformat()

        text = json.dumps(data, indent=2, ensure_ascii=False)

        # Validate round-trip
        json.loads(text)

        # Write to temp file in same directory (required for atomic rename)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp", prefix="ledger_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)

            # Windows can transiently deny replace if another thread/process is
            # briefly reading the target path. Retry a few times with tiny
            # backoff to preserve atomic semantics under parallel reads.
            last_error: OSError | None = None
            for attempt in range(5):
                try:
                    os.replace(tmp_path, self.path)  # atomic on POSIX and Windows
                    last_error = None
                    break
                except PermissionError as exc:
                    last_error = exc
                    if attempt == 4:
                        break
                    time.sleep(0.01 * (attempt + 1))

            if last_error is not None:
                raise last_error
        except Exception:
            # Clean up temp file on failure
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @staticmethod
    def _transition(task: Task, new_status: TaskStatus) -> None:
        """Validate and apply status transition. Raises InvalidTransition on bad move."""
        if not task.can_transition_to(new_status):
            raise InvalidTransition(
                f"Cannot transition task {task.id!r} "
                f"from {task.status.value!r} to {new_status.value!r}"
            )
        task.status = new_status

    @staticmethod
    def _assert_exists(data: dict[str, Any], task_id: str) -> None:
        if task_id not in data["tasks"]:
            raise TaskNotFound(f"Task {task_id!r} not found in ledger")
