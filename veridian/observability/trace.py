"""
veridian.observability.trace
---------------------------------------------------------------
Run-scoped trace identifier and a JSONL trace sink hook.

The trace_id is held in a contextvar so any code reached from a
runner-scoped call can correlate its log lines to the originating run
or task without threading the id through every function signature.

JsonlTraceHook appends one JSON record per lifecycle event. The hook is
crash-safe in the same way the ledger is: writes are line-buffered and
fsynced on close so a SIGKILL between events loses at most the partial
final line.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = ["JsonlTraceHook", "get_trace_id", "set_trace_id"]

log = logging.getLogger(__name__)

_TRACE_ID: ContextVar[str | None] = ContextVar("veridian_trace_id", default=None)


def get_trace_id() -> str | None:
    """Return the current run's trace id, or None when unset."""
    return _TRACE_ID.get()


def set_trace_id(trace_id: str | None) -> None:
    """Pin the trace id for the current context (a new uuid4 if None)."""
    _TRACE_ID.set(trace_id or uuid.uuid4().hex)


def _event_to_record(method: str, event: Any) -> dict[str, Any]:
    """Serialise an event without raising on unexpected shapes."""
    record: dict[str, Any] = {"hook_method": method}
    trace_id = get_trace_id()
    if trace_id:
        record["trace_id"] = trace_id
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
            if isinstance(payload, dict):
                record.update(payload)
                return record
        except Exception as exc:
            record["_serialise_error"] = str(exc)
    for attr in ("event_type", "run_id", "ts"):
        value = getattr(event, attr, None)
        if value is not None:
            record[attr] = str(value)
    return record


class JsonlTraceHook(BaseHook):
    """Append-only JSONL sink for all lifecycle events.

    The hook activates when ``VERIDIAN_TRACE_PATH`` is set; ``auto_register``
    in :mod:`veridian.observability.setup` handles that wiring. The hook can
    also be constructed and registered directly for tests or custom flows.
    """

    id: ClassVar[str] = "jsonl_trace"
    priority: ClassVar[int] = 5  # after LoggingHook, before behavioural hooks

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def _write(self, method: str, event: Any) -> None:
        record = _event_to_record(method, event)
        try:
            line = json.dumps(record, default=str, separators=(",", ":"))
        except Exception as exc:
            log.warning("jsonl_trace.serialise_failed err=%s", exc)
            return
        with self._lock:
            self._fh.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            self._fh.close()

    def before_run(self, event: Any) -> None:
        self._write("before_run", event)

    def after_run(self, event: Any) -> None:
        self._write("after_run", event)
        self.close()

    def before_task(self, event: Any) -> None:
        self._write("before_task", event)

    def after_task(self, event: Any) -> None:
        self._write("after_task", event)

    def on_failure(self, event: Any) -> None:
        self._write("on_failure", event)

    def on_pause(self, event: Any) -> None:
        self._write("on_pause", event)

    def on_resume(self, event: Any) -> None:
        self._write("on_resume", event)
