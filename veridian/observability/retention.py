"""
veridian.observability.retention
────────────────────────────────
Retention policies for JSONL trace files.

Rules:
- RetentionPolicy is a frozen dataclass (immutable after construction).
- RetentionManager.enforce() reads the JSONL file, filters events, and rewrites
  atomically via tempfile + os.replace.
- Policies are applied in order: max_age → max_size → max_events.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "RetentionManager",
    "RetentionPolicy",
]


# ── RetentionPolicy ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetentionPolicy:
    """
    Configures retention limits for observability trace files.

    All fields are optional (None = no limit for that dimension).
    """

    max_age_hours: int | None = None
    max_size_mb: float | None = None
    max_events: int | None = None


# ── RetentionManager ──────────────────────────────────────────────────────────


class RetentionManager:
    """Enforces a RetentionPolicy on a JSONL trace file."""

    def __init__(self, policy: RetentionPolicy) -> None:
        self._policy = policy

    def enforce(self, trace_file: Path) -> None:
        """
        Read *trace_file*, apply retention rules, and rewrite atomically.

        Policies applied in order: max_age → max_size → max_events.
        """
        if not trace_file.exists():
            return

        raw = trace_file.read_text(encoding="utf-8")
        if not raw.strip():
            return

        events: list[dict[str, Any]] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping malformed JSONL line in %s", trace_file)

        if not events:
            return

        now = time.time()

        # 1. max_age_hours — remove expired events
        if self._policy.max_age_hours is not None:
            events = [e for e in events if not self._is_expired(e, now)]

        # 2. max_size_mb — drop oldest until under limit
        if self._policy.max_size_mb is not None:
            limit_bytes = self._policy.max_size_mb * 1024 * 1024
            events = self._trim_to_size(events, limit_bytes)

        # 3. max_events — keep only the most recent N
        if self._policy.max_events is not None and len(events) > self._policy.max_events:
            events = events[-self._policy.max_events :]

        # Atomic rewrite
        self._atomic_write(trace_file, events)

    def _is_expired(self, event: dict[str, Any], now: float) -> bool:
        """Return True if the event's timestamp is older than max_age_hours."""
        ts = event.get("timestamp")
        if ts is None:
            return False  # no timestamp → keep
        try:
            age_hours = (now - float(ts)) / 3600
        except (TypeError, ValueError):
            return False
        max_age = self._policy.max_age_hours
        if max_age is None:
            return False
        return age_hours > max_age

    @staticmethod
    def _trim_to_size(events: list[dict[str, Any]], limit_bytes: float) -> list[dict[str, Any]]:
        """Drop oldest events until serialised size is under *limit_bytes*."""
        while events:
            size = sum(len(json.dumps(e, ensure_ascii=False).encode()) + 1 for e in events)
            if size <= limit_bytes:
                break
            events = events[1:]  # drop oldest
        return events

    @staticmethod
    def _atomic_write(path: Path, events: list[dict[str, Any]]) -> None:
        """Rewrite *path* atomically with the retained events."""
        content = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)

        fd, tmp = tempfile.mkstemp(
            dir=path.parent,
            prefix=".retention_",
            suffix=".tmp",
        )
        try:
            os.write(fd, content.encode())
            os.close(fd)
            os.replace(tmp, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            log.exception("Failed to atomically rewrite %s during retention enforcement", path)
