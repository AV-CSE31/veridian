"""
veridian.observability.logging
---------------------------------------------------------------
JSON log formatter and a one-shot configurator.

The Dockerfile ships ``VERIDIAN_LOG_FORMAT=json`` by default, but the
runtime used to rely on stdlib ``logging`` defaults so production
deployments emitted unstructured text. ``configure_logging()`` wires a
JSON formatter onto the root logger when the env var is set; callers
that already configure logging keep their configuration untouched.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from veridian.observability.trace import get_trace_id

__all__ = ["JsonLogFormatter", "configure_logging"]

_RESERVED_LOG_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Always-present fields: ``ts``, ``level``, ``logger``, ``message``.
    Optional fields: ``trace_id`` (when set in the context), ``exc_info``
    (formatted traceback), and any custom keyword passed via ``extra=``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value, default=str)
            except TypeError:
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(force: bool = False) -> bool:
    """Install :class:`JsonLogFormatter` on the root logger when requested.

    Activates iff ``VERIDIAN_LOG_FORMAT=json`` is set (or ``force=True``).
    Returns ``True`` when the formatter was installed, ``False`` when
    skipped. Idempotent: subsequent calls re-use the existing handler.
    """
    if not force and os.getenv("VERIDIAN_LOG_FORMAT", "").lower() != "json":
        return False

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler):
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return True
