"""
veridian.ledger.ledger
---------------------------------------------------------------
TaskLedger --- the single source of truth for all task state.

RULES:
- Ledger is the ONLY object allowed to transition task status.
- All writes are atomic (temp-file --- rename via os.replace).
- FileLock ensures single writer across processes.
- reset_in_progress() MUST be called at the start of every run().
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
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

from veridian.core.atomic_io import _fsync_enabled
from veridian.core.exceptions import (
    InvalidTransition,
    LedgerCorrupted,
    TaskAlreadyClaimed,
    TaskNotFound,
    TaskNotPaused,
    VeridianConfigError,
)
from veridian.core.task import LedgerStats, Task, TaskPriority, TaskResult, TaskStatus
from veridian.ledger.wal import GENESIS_HASH, WalHead, WalHeadStore, WalLog, WalReplay

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_WAL_COMPACT_ENTRIES_DEFAULT = 1000


def _json_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LedgerCorrupted(f"ledger value is not canonical JSON: {exc}") from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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
        self._wal_enabled = os.getenv("VERIDIAN_LEDGER_WAL", "1").strip() != "0"
        self._wal = WalLog(self.path.with_name(f"{self.path.name}.wal"))
        self._wal_head = WalHeadStore(self.path.with_name(f"{self.path.name}.wal.head"))
        self._ledger_id = ""
        self._generation = 1
        try:
            self._wal_compact_entries = int(
                os.getenv(
                    "VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES",
                    str(_WAL_COMPACT_ENTRIES_DEFAULT),
                )
            )
        except ValueError as exc:
            raise VeridianConfigError("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES must be an int") from exc
        if self._wal_compact_entries < 1:
            raise VeridianConfigError("VERIDIAN_LEDGER_WAL_COMPACT_ENTRIES must be positive")

        # Bootstrap and recovery share the writer lock.  In particular, a
        # missing snapshot is never mistaken for a new ledger when a durable
        # WAL proves that an earlier ledger existed.
        with self._lock:
            if not self.path.exists():
                self._promote_snapshot_candidate()
            if self.path.exists():
                try:
                    snapshot = self._read_snapshot()
                except LedgerCorrupted:
                    replay = self._wal.replay() if self._wal_enabled else None
                    head = self._wal_head.read() if self._wal_enabled else None
                    if (
                        replay is None
                        or replay.entry_count == 0
                        or replay.ledger_id is None
                        or replay.generation != 1
                        or head is None
                        or head.last_seq == 0
                    ):
                        raise
                    self._validate_replay_anchor(replay, head)
                    self._validate_replay_tasks(replay)
                    reason = "empty" if self.path.stat().st_size == 0 else "invalid"
                    self._quarantine_corrupt_ledger(reason)
                    self._ledger_id = replay.ledger_id
                    self._generation = replay.generation
                    snapshot = {"schema_version": SCHEMA_VERSION, "tasks": {}}
                    self._write_raw(snapshot)
                metadata = snapshot.get("_ledger")
                if isinstance(metadata, dict):
                    ledger_id = metadata.get("id")
                    generation = metadata.get("generation")
                    if (
                        not isinstance(ledger_id, str)
                        or not ledger_id
                        or not isinstance(generation, int)
                        or isinstance(generation, bool)
                        or generation < 1
                    ):
                        raise LedgerCorrupted("ledger snapshot metadata is invalid")
                    self._ledger_id = ledger_id
                    self._generation = generation
                else:
                    if self._wal_enabled and self._wal.size():
                        raise LedgerCorrupted(
                            "legacy snapshot cannot be paired with an existing WAL"
                        )
                    self._ledger_id = uuid.uuid4().hex
                    self._write_raw(snapshot)
            elif self._wal_enabled and self._wal.size():
                replay = self._wal.replay()
                head = self._wal_head.read()
                if (
                    replay.entry_count == 0
                    or replay.ledger_id is None
                    or replay.generation != 1
                    or head is None
                    or head.last_seq == 0
                ):
                    raise LedgerCorrupted("snapshot is missing and WAL cannot reconstruct its base")
                self._validate_replay_anchor(replay, head)
                self._validate_replay_tasks(replay)
                self._ledger_id = replay.ledger_id
                self._generation = replay.generation
                self._write_raw({"schema_version": SCHEMA_VERSION, "tasks": {}})
            elif self._wal_enabled and (
                self._wal_head.read() is not None
                or any(self.path.parent.glob(f"{self.path.name}.wal.g*.sealed"))
            ):
                raise LedgerCorrupted(
                    "ledger snapshot is missing and history cannot be rebased safely"
                )
            else:
                self._ledger_id = uuid.uuid4().hex
                self._write_raw({"schema_version": SCHEMA_VERSION, "tasks": {}})
            if self._wal_enabled:
                self._reconcile_wal_generation()
            self._cleanup_atomic_temps()

    # ------ READ INTERFACE ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

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
        raw_tasks = data["tasks"].values()
        done_ids = {d["id"] for d in raw_tasks if d.get("status", "pending") == "done"}

        # RV3-001: Resume-first policy --- surface PAUSED tasks before PENDING ones.
        if include_paused:
            paused = [
                Task.from_dict(d)
                for d in raw_tasks
                if d.get("status", "pending") == "paused"
                and (phase is None or d.get("phase", "default") == phase)
            ]
            if paused:
                paused.sort(key=lambda t: (-t.priority, t.created_at))
                return paused[0]

        candidates = [
            Task.from_dict(d)
            for d in raw_tasks
            if d.get("status", "pending") == "pending"
            and (phase is None or d.get("phase", "default") == phase)
            and (
                not respect_dependencies or all(dep in done_ids for dep in d.get("depends_on", []))
            )
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
        """Return filtered list of tasks. Returns copies.

        Filters are applied at the raw-dict level (cheap string comparison)
        *before* materialising :class:`Task` instances. On large ledgers the
        runner frequently calls ``list(status=PENDING)`` and skipping
        ``Task.from_dict`` for the DONE/FAILED majority is the bigger cost
        saving than caching the parsed JSON.
        """
        data = self._read_raw()
        raw_tasks = data["tasks"].values()

        sv = (
            status.value
            if isinstance(status, TaskStatus)
            else status
            if isinstance(status, str)
            else None
        )

        def _matches(raw: dict[str, Any]) -> bool:
            if sv is not None and raw.get("status") != sv:
                return False
            if phase is not None and raw.get("phase") != phase:
                return False
            return not (priority_gte is not None and raw.get("priority", 0) < priority_gte)

        tasks = [Task.from_dict(t) for t in raw_tasks if _matches(t)]
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks

    def stats(self) -> LedgerStats:
        """Compute current ledger statistics."""
        data = self._read_raw()
        raw_tasks = data["tasks"].values()

        by_status: dict[str, int] = {}
        phases: dict[str, int] = {}
        total_tokens = 0
        total_retries = 0

        for task_dict in raw_tasks:
            status = TaskStatus(task_dict.get("status", "pending"))
            by_status[status.value] = by_status.get(status.value, 0) + 1
            if status == TaskStatus.PENDING:
                phase = task_dict.get("phase", "default")
                phases[phase] = phases.get(phase, 0) + 1
            total_retries += task_dict.get("retry_count", 0)
            result = task_dict.get("result")
            if result:
                total_tokens += result.get("token_usage", {}).get("total_tokens", 0)

        n = len(raw_tasks)
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
        raw_tasks = sorted(
            data["tasks"].values(),
            key=lambda task: -task.get("priority", TaskPriority.NORMAL),
        )
        seen: list[str] = []
        for task_dict in raw_tasks:
            phase = task_dict.get("phase", "default")
            if phase not in seen:
                seen.append(phase)
        return seen

    # ------ WRITE INTERFACE ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def add(self, tasks: builtins.list[Task], skip_duplicates: bool = True) -> int:
        """
        Add tasks to the ledger. Returns count added.
        If skip_duplicates=True and a task with the same id exists, skip it.
        """
        added = 0
        changed: builtins.list[dict[str, Any]] = []
        self._validate_tasks(tasks)
        with self._lock:
            data = self._read_raw()
            for task in tasks:
                if task.id in data["tasks"] and skip_duplicates:
                    continue
                data["tasks"][task.id] = task.to_dict()
                changed.append(data["tasks"][task.id])
                added += 1
            if changed:
                self._commit(data, changed)
        log.debug("ledger.add count=%d skip_dup=%s", added, skip_duplicates)
        return added

    @staticmethod
    def _validate_tasks(tasks: builtins.list[Task]) -> None:
        """Fail fast for verifier IDs that can never run."""
        from veridian.verify.base import registry  # noqa: PLC0415

        for task in tasks:
            if not registry.has(task.verifier_id):
                registry.get(task.verifier_id, None)

    def claim(self, task_id: str, runner_id: str) -> Task:
        """
        Transition PENDING --- IN_PROGRESS. Idempotent for the same runner.
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
                # Same runner re-claiming an already IN_PROGRESS task --- idempotent
                return task

            self._transition(task, TaskStatus.IN_PROGRESS)
            task.claimed_by = runner_id
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._commit(data, [data["tasks"][task_id]])

        return task

    def submit_result(self, task_id: str, result: TaskResult) -> Task:
        """IN_PROGRESS --- VERIFYING. Does NOT mark DONE --- verifier does that."""
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.VERIFYING)
            task.result = result
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._commit(data, [data["tasks"][task_id]])
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
            self._commit(data, [data["tasks"][task_id]])
        return task

    def mark_done(self, task_id: str, result: TaskResult) -> Task:
        """VERIFYING --- DONE. Called ONLY by VeridianRunner after verifier passes."""
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
            self._commit(data, [data["tasks"][task_id]])
        self.log(f"[DONE] {task_id} --- {task.title[:60]}")
        return task

    def mark_failed(self, task_id: str, error: str) -> Task:
        """
        --- FAILED. Auto-transitions to ABANDONED if retry_count > max_retries.
        Increments retry_count. Stores error as last_error (for next prompt).
        ABANDONED path: IN_PROGRESS --- FAILED --- ABANDONED (respects state machine).
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
                # Two-step: FAILED --- ABANDONED (state machine compliant)
                self._transition(task, TaskStatus.ABANDONED)
                log.warning("task.abandoned id=%s retries=%d", task_id, task.retry_count)

            data["tasks"][task_id] = task.to_dict()
            self._commit(data, [data["tasks"][task_id]])
        return task

    def skip(self, task_id: str, reason: str = "") -> Task:
        """--- SKIPPED. Terminal. Use for human-curated exclusions."""
        with self._lock:
            data = self._read_raw()
            self._assert_exists(data, task_id)
            task = Task.from_dict(data["tasks"][task_id])
            self._transition(task, TaskStatus.SKIPPED)
            task.last_error = reason
            task.updated_at = datetime.now(tz=UTC)
            data["tasks"][task_id] = task.to_dict()
            self._commit(data, [data["tasks"][task_id]])
        return task

    def pause(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Task:
        """
        RV3-001: IN_PROGRESS --- PAUSED. Persists pause metadata in
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
            self._commit(data, [data["tasks"][task_id]])
        log.info("ledger.pause task_id=%s reason=%s", task_id, reason[:60])
        self.log(f"[PAUSE] {task_id} --- {reason[:80]}")
        return task

    def resume(self, task_id: str, runner_id: str) -> Task:
        """
        RV3-001: PAUSED --- IN_PROGRESS. Increments resume_count, sets claimed_by.
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
            self._commit(data, [data["tasks"][task_id]])
        log.info("ledger.resume task_id=%s runner_id=%s", task_id, runner_id)
        self.log(f"[RESUME] {task_id}")
        return task

    def reset_in_progress(self, runner_id: str | None = None) -> int:
        """
        CRITICAL: Call this at the start of EVERY run().
        Resets active tasks back to PENDING (crash recovery).
        If runner_id given: only reset tasks claimed by that runner.
        Returns count reset.

        RV3-001 guarantee: PAUSED tasks are NEVER reset. Their pause payload is
        preserved so they can be resumed on next run().
        """
        reset = 0
        with self._lock:
            data = self._read_raw()
            changed: builtins.list[dict[str, Any]] = []
            for task_dict in data["tasks"].values():
                if task_dict.get("status") not in {"in_progress", "verifying"}:
                    continue
                if runner_id and task_dict.get("claimed_by") != runner_id:
                    continue
                task_dict["status"] = "pending"
                task_dict["claimed_by"] = None
                task_dict["updated_at"] = datetime.now(tz=UTC).isoformat()
                changed.append(task_dict)
                reset += 1
            if reset:
                self._commit(data, changed)

        if reset:
            log.info("ledger.reset_in_progress count=%d", reset)
            self.log(f"[RESET] {reset} stale active tasks --- PENDING (crash recovery)")
        return reset

    def reset_failed(self, task_ids: builtins.list[str] | None = None) -> int:
        """
        Reset FAILED/ABANDONED tasks --- PENDING for re-queue.
        retry_count is preserved so the abandonment threshold remains accurate
        across multiple reset cycles.
        """
        reset = 0
        with self._lock:
            data = self._read_raw()
            changed: builtins.list[dict[str, Any]] = []
            for tid, task_dict in data["tasks"].items():
                if task_ids and tid not in task_ids:
                    continue
                if task_dict.get("status") not in {"failed", "abandoned"}:
                    continue
                task_dict["status"] = "pending"
                task_dict["last_error"] = None
                task_dict["updated_at"] = datetime.now(tz=UTC).isoformat()
                changed.append(task_dict)
                reset += 1
            if reset:
                self._commit(data, changed)
        return reset

    # ------ PROGRESS LOG ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

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

    # ------ INTERNAL ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _read_raw(self) -> dict[str, Any]:
        """Return the validated snapshot with the durable WAL applied."""
        replay: WalReplay | None = None
        try:
            data = self._read_snapshot()
        except (FileNotFoundError, LedgerCorrupted):
            if not self._wal_enabled or self._generation != 1:
                raise
            replay = self._wal.replay(
                expected_ledger_id=self._ledger_id,
                expected_generation=self._generation,
            )
            head = self._wal_head.read()
            if replay.entry_count == 0 or head is None:
                raise
            self._validate_wal_anchor(replay, head)
            data = {
                "schema_version": SCHEMA_VERSION,
                "tasks": {},
                "_ledger": {"id": self._ledger_id, "generation": self._generation},
            }
        if self._wal_enabled:
            replay = replay or self._wal.replay(
                expected_ledger_id=self._ledger_id,
                expected_generation=self._generation,
            )
            head = self._wal_head.read()
            if head is None:
                raise LedgerCorrupted("WAL anchor is missing")
            self._validate_wal_anchor(replay, head)
            for task_dict, sequence in zip(
                replay.upserts,
                replay.upsert_sequences,
                strict=True,
            ):
                if sequence > head.last_seq:
                    continue
                task_id = task_dict["id"]
                self._validate_task_entry(task_id, task_dict)
                data["tasks"][task_id] = task_dict
        return data

    def _read_snapshot(self) -> dict[str, Any]:
        """Read the canonical snapshot and reject corruption without healing."""
        return self._read_snapshot_file(self.path)

    def _read_snapshot_file(self, path: Path) -> dict[str, Any]:
        """Validate one snapshot artifact without mutating ledger state."""
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise LedgerCorrupted("ledger.json is empty")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise LedgerCorrupted("ledger.json root must be an object")
            checksum = payload.get("_checksum")
            if checksum is None:
                if "_ledger" in payload:
                    raise LedgerCorrupted("ledger snapshot checksum is missing")
            elif not isinstance(checksum, str):
                raise LedgerCorrupted("ledger snapshot checksum is invalid")
            else:
                unsigned = dict(payload)
                del unsigned["_checksum"]
                if checksum != _json_digest(unsigned):
                    raise LedgerCorrupted("ledger snapshot checksum mismatch")
            return self._normalize_legacy_shape(dict(payload))
        except json.JSONDecodeError as e:
            raise LedgerCorrupted(f"ledger.json is malformed: {e}") from e

    def _promote_snapshot_candidate(self) -> bool:
        """Promote the newest unambiguous, checksummed snapshot temp."""
        candidates = list(self.path.parent.glob(f".{self.path.name}.snapshot.*.tmp"))
        if not candidates:
            return False
        valid: list[tuple[int, str, Path]] = []
        for candidate in candidates:
            try:
                payload = self._read_snapshot_file(candidate)
            except (LedgerCorrupted, OSError):
                continue
            metadata = payload.get("_ledger")
            if not isinstance(metadata, dict):
                continue
            generation = metadata.get("generation")
            checksum = payload.get("_checksum")
            if (
                isinstance(generation, int)
                and not isinstance(generation, bool)
                and isinstance(checksum, str)
            ):
                valid.append((generation, checksum, candidate))
        if not valid:
            raise LedgerCorrupted("snapshot temp artifacts exist but none are valid")
        newest_generation = max(item[0] for item in valid)
        newest = [item for item in valid if item[0] == newest_generation]
        if len({item[1] for item in newest}) != 1:
            raise LedgerCorrupted("snapshot temp artifacts are ambiguous")
        chosen = newest[0][2]
        self._replace_snapshot_file(chosen, self.path)
        self._sync_parent_directory()
        return True

    def _cleanup_atomic_temps(self) -> int:
        """Remove abandoned temps only after canonical state is validated."""
        candidates = {
            *self.path.parent.glob(f".{self.path.name}.snapshot.*.tmp"),
            *self.path.parent.glob(f".{self.path.name}.wal.*.tmp"),
        }
        removed = 0
        for candidate in candidates:
            try:
                candidate.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                log.warning("ledger.temp_cleanup_failed path=%s", candidate)
        if removed:
            log.info("ledger.temp_cleanup count=%d ledger=%s", removed, self.path)
        return removed

    def _commit(self, data: dict[str, Any], changed: builtins.list[dict[str, Any]]) -> None:
        """Persist a mutation before returning acknowledgement to the caller."""
        if not self._wal_enabled:
            self._write_raw(data)
            return

        tasks = [dict(task) for task in changed]
        if not tasks:
            return
        replay = self._wal.replay(
            expected_ledger_id=self._ledger_id,
            expected_generation=self._generation,
        )
        head = self._wal_head.read()
        if head is None:
            raise LedgerCorrupted("WAL anchor is missing")
        self._validate_wal_anchor(replay, head)
        if replay.last_seq > head.last_seq:
            self._wal.truncate_to_sequence(replay, head.last_seq, fsync=_fsync_enabled())
            replay = self._wal.replay(
                expected_ledger_id=self._ledger_id,
                expected_generation=self._generation,
            )
        if replay.has_invalid_tail:
            self._wal.repair(replay, fsync=_fsync_enabled())
        checksum = self._wal.append(
            tasks,
            ledger_id=self._ledger_id,
            generation=self._generation,
            seq=replay.last_seq + 1,
            previous_hash=replay.last_hash,
            fsync=_fsync_enabled(),
        )
        self._wal_head.write(
            WalHead(
                ledger_id=self._ledger_id,
                generation=self._generation,
                last_seq=replay.last_seq + 1,
                last_hash=checksum,
            ),
            fsync=_fsync_enabled(),
        )
        if replay.entry_count + 1 >= self._wal_compact_entries:
            self._compact(data)

    def _compact(self, data: dict[str, Any]) -> None:
        """Checkpoint the current state and rotate the validated WAL generation."""
        old_generation = self._generation
        replay = self._wal.replay(
            expected_ledger_id=self._ledger_id,
            expected_generation=old_generation,
        )
        head = self._wal_head.read()
        if head is None:
            raise LedgerCorrupted("WAL anchor is missing during compaction")
        self._validate_wal_anchor(replay, head)
        self._generation = old_generation + 1
        try:
            self._write_raw(data)
            self._wal.seal(replay, fsync=_fsync_enabled())
            self._wal.reset(fsync=_fsync_enabled())
            self._wal_head.write(
                WalHead(
                    ledger_id=self._ledger_id,
                    generation=self._generation,
                    last_seq=0,
                    last_hash=GENESIS_HASH,
                ),
                fsync=_fsync_enabled(),
            )
        except Exception:
            # The snapshot may already advertise the new generation.  Keep the
            # in-memory identity aligned with disk so a subsequent operation
            # fails closed instead of appending to the old generation.
            if not self.path.exists():
                self._generation = old_generation
            raise

    def _reconcile_wal_generation(self) -> None:
        """Complete an interrupted generation rotation under the ledger lock."""
        replay = self._wal.replay()
        head = self._wal_head.read()
        fsync = _fsync_enabled()

        if replay.entry_count:
            self._validate_replay_tasks(replay)
            if replay.ledger_id != self._ledger_id:
                raise LedgerCorrupted("WAL is bound to a different ledger")
            if head is None:
                raise LedgerCorrupted("WAL anchor is missing")
            self._validate_replay_anchor(replay, head)
            if replay.has_invalid_tail:
                self._wal.repair(replay, fsync=fsync)
                replay = self._wal.replay(
                    expected_ledger_id=self._ledger_id,
                    expected_generation=head.generation,
                )
            if replay.last_seq > head.last_seq:
                self._wal.truncate_to_sequence(replay, head.last_seq, fsync=fsync)
                replay = self._wal.replay(
                    expected_ledger_id=self._ledger_id,
                    expected_generation=head.generation,
                )
                self._validate_replay_anchor(replay, head)
            if replay.generation == self._generation:
                return
            if replay.generation == self._generation - 1:
                self._wal.seal(replay, fsync=fsync)
                self._wal.reset(fsync=fsync)
                self._write_empty_wal_head(fsync=fsync)
                return
            raise LedgerCorrupted("snapshot and WAL generations cannot be reconciled")

        if head is None:
            self._write_empty_wal_head(fsync=fsync)
            return
        if head.ledger_id != self._ledger_id:
            raise LedgerCorrupted("WAL anchor is bound to a different ledger")
        if head.generation == self._generation:
            if head.last_seq != 0 or head.last_hash != GENESIS_HASH:
                raise LedgerCorrupted(f"WAL rollback detected: anchor={head.last_seq}, log=0")
            return
        if head.generation == self._generation - 1:
            archive = self._wal.sealed_path(
                generation=head.generation,
                last_hash=head.last_hash,
            )
            archive_replay = WalLog(archive).replay(
                expected_ledger_id=self._ledger_id,
                expected_generation=head.generation,
            )
            self._validate_replay_anchor(archive_replay, head)
            self._wal.reset(fsync=fsync)
            self._write_empty_wal_head(fsync=fsync)
            return
        raise LedgerCorrupted("snapshot and WAL anchor generations cannot be reconciled")

    def _write_empty_wal_head(self, *, fsync: bool) -> None:
        self._wal_head.write(
            WalHead(
                ledger_id=self._ledger_id,
                generation=self._generation,
                last_seq=0,
                last_hash=GENESIS_HASH,
            ),
            fsync=fsync,
        )

    def _validate_replay_anchor(self, replay: WalReplay, head: WalHead) -> None:
        if replay.ledger_id != head.ledger_id or replay.generation != head.generation:
            raise LedgerCorrupted("WAL anchor is bound to a different log generation")
        if replay.last_seq < head.last_seq:
            raise LedgerCorrupted(
                f"WAL rollback detected: anchor={head.last_seq}, log={replay.last_seq}"
            )
        anchored_hash = GENESIS_HASH if head.last_seq == 0 else replay.hashes[head.last_seq - 1]
        if anchored_hash != head.last_hash:
            raise LedgerCorrupted("WAL anchor does not match the log hash chain")

    def _validate_wal_anchor(self, replay: WalReplay, head: WalHead) -> None:
        """Reject rollback or substitution relative to the current durable head."""
        if head.ledger_id != self._ledger_id or head.generation != self._generation:
            raise LedgerCorrupted("WAL anchor is bound to a different ledger generation")
        self._validate_replay_anchor(replay, head)

    def _quarantine_corrupt_ledger(self, reason: str) -> None:
        """Preserve a corrupt ledger artifact before self-healing."""
        if not self.path.exists():
            return
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
        quarantine = self.path.with_name(f"{self.path.name}.{reason}.{stamp}.corrupt")
        with contextlib.suppress(OSError):
            os.replace(self.path, quarantine)

    @staticmethod
    def _validate_replay_tasks(replay: WalReplay) -> None:
        for task_dict in replay.upserts:
            task_id = task_dict["id"]
            TaskLedger._validate_task_entry(task_id, task_dict)

    @staticmethod
    def _validate_task_entry(task_id: str, item: dict[str, Any]) -> None:
        if not task_id or item.get("id") != task_id:
            raise LedgerCorrupted("ledger task entry is invalid")
        try:
            parsed = Task.from_dict(item)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise LedgerCorrupted(f"ledger task {task_id!r} is invalid: {exc}") from exc
        if parsed.id != task_id:
            raise LedgerCorrupted(f"ledger task {task_id!r} is invalid")

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
            for task_id, item in raw_tasks.items():
                if not isinstance(task_id, str) or not task_id or not isinstance(item, dict):
                    raise LedgerCorrupted("ledger task entry is invalid")
                TaskLedger._validate_task_entry(task_id, item)
                tasks[task_id] = item
        elif isinstance(raw_tasks, list):
            for item in raw_tasks:
                if not isinstance(item, dict):
                    raise LedgerCorrupted("legacy ledger task entry is invalid")
                task_id = item.get("id")
                if not isinstance(task_id, str) or not task_id or task_id in tasks:
                    raise LedgerCorrupted("legacy ledger task entry is invalid")
                TaskLedger._validate_task_entry(task_id, item)
                tasks[task_id] = item
        else:
            raise LedgerCorrupted("ledger tasks must be an object or legacy list")

        data["tasks"] = tasks
        version = data.get("schema_version")
        if version is not None and (
            not isinstance(version, int) or isinstance(version, bool) or version < 1
        ):
            raise LedgerCorrupted("ledger schema_version is invalid")
        if version is None:
            data["schema_version"] = SCHEMA_VERSION
        return data

    def _write_raw(self, data: dict[str, Any]) -> None:
        """
        Atomic write via temp file + os.replace().

        Serialization uses compact JSON (no ``indent=2``) on the hot path ---
        every state transition (claim, submit_result, mark_done, ---) goes
        through here and the indented form roughly doubles wall-clock per
        write on multi-hundred-task ledgers. Set ``VERIDIAN_LEDGER_INDENT=1``
        to opt back into pretty-printed output (useful when hand-inspecting
        ledger.json in development).
        """
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = datetime.now(tz=UTC).isoformat()
        data["_ledger"] = {"id": self._ledger_id, "generation": self._generation}
        data.pop("_checksum", None)
        data["_checksum"] = _json_digest(data)

        indent = 2 if os.getenv("VERIDIAN_LEDGER_INDENT") == "1" else None
        text = json.dumps(data, indent=indent, ensure_ascii=False, allow_nan=False)

        # Write to temp file in same directory (required for atomic rename)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent,
            suffix=".tmp",
            prefix=f".{self.path.name}.snapshot.",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                if _fsync_enabled():
                    os.fsync(f.fileno())

            self._replace_snapshot_file(Path(tmp_path), self.path)
            self._sync_parent_directory()
        except Exception:
            # Clean up temp file on failure
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @staticmethod
    def _replace_snapshot_file(source: Path, target: Path) -> None:
        """Retry the Windows sharing-violation window without weakening atomicity."""
        last_error: PermissionError | None = None
        for attempt in range(5):
            try:
                os.replace(source, target)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(0.01 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _sync_parent_directory(self) -> None:
        """Persist snapshot directory metadata where the platform supports it."""
        if not _fsync_enabled() or os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
