"""
veridian.observability
---------------------------------------------------------------
Optional structured logging, trace export, and alerting primitives.

Public surface:
  - JsonLogFormatter     - logging.Formatter that emits JSON lines
  - configure_logging()  - honour VERIDIAN_LOG_FORMAT=json on the root logger
  - JsonlTraceHook       - writes typed lifecycle events to a JSONL sink
  - AlertHook / WebhookAlertHook - ABC + reference impl for failure alerts
  - auto_register(hooks) - register trace + alert hooks per environment

All components are opt-in: they activate only when their environment
variable is set, so a default ``import veridian`` retains current
behaviour.
"""

from __future__ import annotations

from veridian.observability.alerts import AlertHook, WebhookAlertHook
from veridian.observability.logging import JsonLogFormatter, configure_logging
from veridian.observability.setup import auto_register
from veridian.observability.trace import JsonlTraceHook, get_trace_id, set_trace_id

__all__ = [
    "AlertHook",
    "JsonLogFormatter",
    "JsonlTraceHook",
    "WebhookAlertHook",
    "auto_register",
    "configure_logging",
    "get_trace_id",
    "set_trace_id",
]
