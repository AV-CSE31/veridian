"""
veridian.observability.ingest
─────────────────────────────
Scalable batch/stream ingest pipeline for observability events.

Rules:
- IngestSink is an ABC — all concrete sinks must subclass it.
- JSONLSink uses tempfile + os.replace for atomic writes.
- IngestBuffer is thread-safe via threading.Lock / threading.Condition.
- BackpressurePolicy controls behavior when the buffer is full.
"""

from __future__ import annotations

import contextlib
import enum
import json
import logging
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "BackpressurePolicy",
    "IngestBuffer",
    "IngestPipeline",
    "IngestSink",
    "JSONLSink",
]


# ── IngestSink ABC ─────────────────────────────────────────────────────────────


class IngestSink(ABC):
    """Abstract base for event sinks that receive batches of events."""

    @abstractmethod
    def write(self, events: list[dict[str, Any]]) -> None:
        """Write a batch of events to the sink."""

    @abstractmethod
    def flush(self) -> None:
        """Flush any internal buffers in the sink."""


# ── BackpressurePolicy ─────────────────────────────────────────────────────────


class BackpressurePolicy(enum.Enum):
    """Behavior when the ingest buffer is full."""

    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"


# ── JSONLSink ──────────────────────────────────────────────────────────────────


class JSONLSink(IngestSink):
    """
    Writes events to a JSONL file using atomic tempfile + os.replace append
    pattern.  Thread-safe via an internal lock.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, events: list[dict[str, Any]]) -> None:
        """Atomically append *events* to the JSONL file."""
        if not events:
            return

        new_lines = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            existing = b""
            if self._path.exists():
                existing = self._path.read_bytes()

            content = existing
            if content and not content.endswith(b"\n"):
                content += b"\n"
            content += new_lines.encode()

            fd, tmp = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=".ingest_",
                suffix=".tmp",
            )
            try:
                os.write(fd, content)
                os.close(fd)
                os.replace(tmp, self._path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                log.exception("Failed to write %d events to JSONL sink", len(events))

    def flush(self) -> None:
        """No-op — JSONLSink writes are already flushed on each write()."""


# ── IngestBuffer ───────────────────────────────────────────────────────────────


class IngestBuffer:
    """
    Thread-safe event buffer that flushes to a sink when batch_size is
    reached or when flush_interval_seconds has elapsed since the last flush.
    """

    def __init__(
        self,
        batch_size: int = 100,
        flush_interval_seconds: float = 5.0,
        max_buffer_size: int = 10000,
        backpressure: BackpressurePolicy = BackpressurePolicy.BLOCK,
    ) -> None:
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._max_buffer_size = max_buffer_size
        self._backpressure = backpressure

        self._buffer: deque[dict[str, Any]] = deque()
        self._condition = threading.Condition(threading.Lock())
        self._last_flush_time = time.monotonic()

    def emit(self, event: dict[str, Any], *, sink: IngestSink) -> None:
        """
        Add an event to the buffer.  If the buffer is full, behavior depends
        on the backpressure policy (BLOCK waits, DROP_OLDEST discards).
        Auto-flushes to *sink* when batch_size or flush_interval is hit.
        """
        with self._condition:
            if len(self._buffer) >= self._max_buffer_size:
                if self._backpressure is BackpressurePolicy.DROP_OLDEST:
                    self._buffer.popleft()
                else:
                    # BLOCK: wait until space is available
                    while len(self._buffer) >= self._max_buffer_size:
                        self._condition.wait(timeout=0.1)

            self._buffer.append(event)
            should_flush = self._should_flush()

        if should_flush:
            self.flush(sink=sink)

    def flush(self, *, sink: IngestSink) -> None:
        """Drain the buffer and write all events to *sink*."""
        with self._condition:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()
            self._last_flush_time = time.monotonic()
            self._condition.notify_all()

        sink.write(batch)

    def _should_flush(self) -> bool:
        """Check batch_size and time interval triggers (caller holds lock)."""
        if len(self._buffer) >= self._batch_size:
            return True
        elapsed = time.monotonic() - self._last_flush_time
        return elapsed >= self._flush_interval


# ── IngestPipeline ─────────────────────────────────────────────────────────────


class IngestPipeline:
    """
    Routes events through optional filters, into the buffer, then to the sink.
    """

    def __init__(
        self,
        sink: IngestSink,
        buffer: IngestBuffer,
        filters: list[Callable[[dict[str, Any]], bool]] | None = None,
    ) -> None:
        self._sink = sink
        self._buffer = buffer
        self._filters: list[Callable[[dict[str, Any]], bool]] = filters or []

    def emit(self, event: dict[str, Any]) -> None:
        """Apply filters and buffer the event for eventual sink write."""
        for f in self._filters:
            if not f(event):
                return  # filtered out
        self._buffer.emit(event, sink=self._sink)

    def shutdown(self) -> None:
        """Flush remaining buffered events to the sink."""
        self._buffer.flush(sink=self._sink)
