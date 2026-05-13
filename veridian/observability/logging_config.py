"""
veridian.observability.logging_config
─────────────────────────────────────
Stdlib-only logging helpers for production deployments.

* :class:`JsonLogFormatter` emits one JSON object per log record, including
  the structured fields Veridian already puts in messages (``run_id``,
  ``task_id``, …) — so log aggregators (Loki, ELK, Datadog) can parse the
  feed without regex.

* :func:`configure_logging` is a one-call setup that honours the
  ``VERIDIAN_LOG_LEVEL`` / ``VERIDIAN_LOG_FORMAT`` env vars and is
  idempotent: calling it twice will not double the handler chain on the
  root logger.

This module avoids any FastAPI / structlog / loguru dependency so it can
run in the smallest deployment footprint.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from typing import Any

__all__ = ["JsonLogFormatter", "configure_logging"]


_STANDARD_RECORD_ATTRS = frozenset(
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
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """One-JSON-object-per-line log formatter for production aggregation.

    Includes the canonical fields a log shipper expects (``ts``, ``level``,
    ``logger``, ``message``) plus any non-standard attributes the caller
    attached via ``logging.LoggerAdapter`` or ``logger.X(..., extra={...})``.
    Exception info is appended as a single ``"exc"`` string.
    """

    default_msec_format = "%s.%03dZ"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Propagate any LoggerAdapter / extra={…} fields the caller stamped on.
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_LOCK = threading.Lock()
_CONFIGURED: dict[str, bool] = {"done": False}


def configure_logging(
    level: str | int | None = None,
    *,
    json_format: bool | None = None,
    stream: Any = None,
    force: bool = False,
) -> None:
    """Configure the root logger for Veridian deployments.

    Args:
        level: Log level name (``"INFO"``, ``"DEBUG"``, …) or numeric
            constant. ``None`` reads ``VERIDIAN_LOG_LEVEL`` (defaults to
            ``"INFO"``).
        json_format: If ``True``, install :class:`JsonLogFormatter`. If
            ``None``, read ``VERIDIAN_LOG_FORMAT=json`` to opt in;
            otherwise use a compact human-readable format.
        stream: Defaults to ``sys.stderr`` to follow 12-factor conventions
            (stdout reserved for the program's structured output).
        force: Reapply configuration even if a previous call already
            initialised handlers. Use sparingly; idempotence is the
            default so libraries don't fight each other.
    """
    with _LOCK:
        if _CONFIGURED["done"] and not force:
            return

        env_level = os.getenv("VERIDIAN_LOG_LEVEL", "INFO")
        resolved_level = level if level is not None else env_level
        if isinstance(resolved_level, str):
            resolved_level = resolved_level.upper()

        env_format = os.getenv("VERIDIAN_LOG_FORMAT", "").lower()
        use_json = json_format if json_format is not None else env_format == "json"

        handler = logging.StreamHandler(stream or sys.stderr)
        if use_json:
            handler.setFormatter(JsonLogFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)-7s %(name)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )

        root = logging.getLogger()
        if force:
            # Drop any pre-existing handlers we may be replacing.
            for existing in list(root.handlers):
                root.removeHandler(existing)
        root.setLevel(resolved_level)
        root.addHandler(handler)
        _CONFIGURED["done"] = True
