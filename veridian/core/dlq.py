"""
veridian.core.dlq
──────────────────
Dead Letter Queue — structured failure triage for tasks that exhaust retries.

Failed tasks go here instead of being silently abandoned. Each entry carries
full diagnostic metadata for operator inspection, re-queue, or dismissal.

Triage categories (auto-assigned):
  TRANSIENT  — retry is expected to succeed (timeout, rate limit, transient API error)
  PERMANENT  — task spec or verifier config needs human fix (schema failure, bad input)
  UNKNOWN    — unrecognized pattern; route to human review

Storage: atomic JSON file (same temp-file + os.replace pattern as TaskLedger).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task

log = logging.getLogger(__name__)


# ── Triage Category ───────────────────────────────────────────────────────────


class TriageCategory(StrEnum):
    """Auto-assigned failure category for DLQ entries."""

    TRANSIENT = "transient"   # Retry expected to succeed
    PERMANENT = "permanent"   # Human fix needed on task/verifier config
    UNKNOWN = "unknown"       # Unrecognized — route to human review


# ── Keyword heuristics for auto-categorization ───────────────────────────────

_TRANSIENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "timeout",
        "timed out",
        "ratelimited",
        "rate limit",
        "rate_limit",
        "providerratelimited",
        "executortimeout",
        "connection reset",
        "connection refused",
        "network",
        "transient",
        "retry",
        "503",
        "502",
        "429",
        "unavailable",
        "overloaded",
    }
)

_PERMANENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "schema",
        "schema validation",
        "validation failed",
        "missing required",
        "invalid input",
        "bad task spec",
        "verifier config",
        "permanent",
        "not found",
        "invalid configuration",
        "config error",
        "veridian config",
    }
)


def _categorize(failure_reason: str) -> TriageCategory:
    """Assign a TriageCategory from the failure reason string."""
    lower = failure_reason.lower()

    for keyword in _TRANSIENT_KEYWORDS:
        if keyword in lower:
            return TriageCategory.TRANSIENT

    for keyword in _PERMANENT_KEYWORDS:
        if keyword in lower:
            return TriageCategory.PERMANENT

    return TriageCategory.UNKNOWN


# ── DLQ Entry ────────────────────────────────────────────────────────────────


@dataclass
class DLQEntry:
    """A single entry in the Dead Letter Queue."""

    task_id: str
    task_data: Task
    failure_reason: str
    timestamp: datetime
    retry_count: int
    triage_category: TriageCategory
    # Additional metadata preserved for debugging
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "task_id": self.task_id,
            "task_data": {
                "id": self.task_data.id,
                "title": self.task_data.title,
                "description": self.task_data.description,
                "verifier_id": self.task_data.verifier_id,
                "status": str(self.task_data.status),
                "retry_count": self.task_data.retry_count,
            },
            "failure_reason": self.failure_reason,
            "timestamp": self.timestamp.isoformat(),
            "retry_count": self.retry_count,
            "triage_category": str(self.triage_category),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DLQEntry:
        """Deserialize from JSON-compatible dict."""
        from veridian.core.task import Task, TaskStatus  # local to avoid circular

        task_data_dict = data["task_data"]
        task = Task(
            id=task_data_dict["id"],
            title=task_data_dict["title"],
            description=task_data_dict.get("description", ""),
            verifier_id=task_data_dict.get("verifier_id", ""),
        )
        task.status = TaskStatus(task_data_dict.get("status", "failed"))
        task.retry_count = task_data_dict.get("retry_count", 0)

        return cls(
            task_id=data["task_id"],
            task_data=task,
            failure_reason=data["failure_reason"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            retry_count=data["retry_count"],
            triage_category=TriageCategory(data["triage_category"]),
            extra=data.get("extra", {}),
        )


# ── Dead Letter Queue ─────────────────────────────────────────────────────────


class DeadLetterQueue:
    """
    Structured Dead Letter Queue for failed tasks.

    Stores entries atomically on disk. Each entry carries:
      - full task data for re-queue
      - failure reason for diagnosis
      - auto-assigned triage category (TRANSIENT / PERMANENT / UNKNOWN)
      - retry count for backoff decisions

    Thread-safety: all writes use atomic os.replace(). Not multi-process safe
    without external locking; for single-runner use this is sufficient.
    """

    def __init__(
        self,
        storage_path: str | Path = "dlq.json",
        max_retries: int = 3,
    ) -> None:
        """
        Args:
            storage_path: Path to the JSON file backing the DLQ.
            max_retries: Maximum retry count before a TRANSIENT entry is
                considered permanently exhausted and is_retryable() returns False.
        """
        if max_retries <= 0:
            raise VeridianConfigError(
                f"DeadLetterQueue: 'max_retries' must be > 0, got {max_retries}."
            )
        self.storage_path = Path(storage_path)
        self.max_retries = max_retries
        self._entries: dict[str, DLQEntry] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(
        self,
        task: Task,
        failure_reason: str,
        retry_count: int,
        extra: dict[str, Any] | None = None,
    ) -> DLQEntry:
        """
        Add a failed task to the DLQ.

        Args:
            task: The failed Task object.
            failure_reason: Human-readable description of why the task failed.
            retry_count: Number of attempts already made.
            extra: Optional additional metadata (last error text, verifier id, etc.)

        Returns:
            The created DLQEntry.
        """
        category = _categorize(failure_reason)
        entry = DLQEntry(
            task_id=task.id,
            task_data=task,
            failure_reason=failure_reason,
            timestamp=datetime.now(tz=timezone.utc),
            retry_count=retry_count,
            triage_category=category,
            extra=extra or {},
        )
        self._entries[task.id] = entry
        self._persist()
        log.info(
            "dlq.enqueued task_id=%s category=%s retry_count=%d",
            task.id,
            category,
            retry_count,
        )
        return entry

    def get(self, task_id: str) -> DLQEntry | None:
        """Return the DLQ entry for task_id, or None if not found."""
        return self._entries.get(task_id)

    def list_entries(
        self, category: TriageCategory | None = None
    ) -> list[DLQEntry]:
        """
        Return all DLQ entries, optionally filtered by triage category.

        Args:
            category: If provided, return only entries with this category.
        """
        entries = list(self._entries.values())
        if category is not None:
            entries = [e for e in entries if e.triage_category == category]
        return entries

    def dismiss(self, task_id: str) -> None:
        """
        Remove an entry from the DLQ (operator confirmed it's handled).

        No-op if task_id is not in the DLQ.
        """
        if task_id in self._entries:
            del self._entries[task_id]
            self._persist()
            log.info("dlq.dismissed task_id=%s", task_id)

    def is_retryable(self, entry: DLQEntry) -> bool:
        """
        Return True if the entry is eligible for automatic retry.

        Only TRANSIENT entries below max_retries are retryable.
        PERMANENT and UNKNOWN always require human intervention.
        """
        if entry.triage_category != TriageCategory.TRANSIENT:
            return False
        return entry.retry_count < self.max_retries

    def size(self) -> int:
        """Return the number of entries currently in the DLQ."""
        return len(self._entries)

    def summary(self) -> dict[str, int]:
        """Return counts broken down by triage category plus total."""
        counts = {
            "total": len(self._entries),
            "transient": 0,
            "permanent": 0,
            "unknown": 0,
        }
        for entry in self._entries.values():
            counts[str(entry.triage_category)] += 1
        return counts

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Write current entries to disk atomically (temp-file + os.replace)."""
        data = {task_id: entry.to_dict() for task_id, entry in self._entries.items()}
        payload = {"version": 1, "entries": data}
        with tempfile.NamedTemporaryFile(
            "w",
            dir=self.storage_path.parent,
            delete=False,
            suffix=".tmp",
            encoding="utf-8",
        ) as f:
            json.dump(payload, f, indent=2)
            tmp_path = Path(f.name)
        os.replace(tmp_path, self.storage_path)

    def _load(self) -> None:
        """Load existing entries from disk if the file exists."""
        if not self.storage_path.exists():
            return
        try:
            with self.storage_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            for task_id, entry_dict in payload.get("entries", {}).items():
                self._entries[task_id] = DLQEntry.from_dict(entry_dict)
            log.debug("dlq.loaded count=%d path=%s", len(self._entries), self.storage_path)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning("dlq.load_failed path=%s err=%s — starting empty", self.storage_path, exc)
            self._entries = {}
