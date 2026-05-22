"""
veridian.observability.setup
---------------------------------------------------------------
Wire optional observability hooks from environment configuration.

VeridianRunner calls ``auto_register(self.hooks)`` at construction so
operators get JSON logs, trace export, and alerting by setting env vars
without code changes.

Recognised environment variables:
  VERIDIAN_LOG_FORMAT    "json" enables :class:`JsonLogFormatter`
  VERIDIAN_TRACE_PATH    File path for :class:`JsonlTraceHook` JSONL output
  VERIDIAN_ALERT_WEBHOOK URL for :class:`WebhookAlertHook` POSTs
"""

from __future__ import annotations

import logging
import os

from veridian.hooks.registry import HookRegistry
from veridian.observability.alerts import WebhookAlertHook
from veridian.observability.logging import configure_logging
from veridian.observability.trace import JsonlTraceHook

__all__ = ["auto_register"]

log = logging.getLogger(__name__)


def auto_register(registry: HookRegistry) -> list[str]:
    """Register environment-configured observability hooks.

    Returns the list of hook ids that were attached so callers (tests,
    diagnostics) can assert what wiring happened. Safe to call multiple
    times: duplicates of the same hook type are skipped.
    """
    attached: list[str] = []
    existing_ids = {getattr(h, "id", "") for h in registry.hooks}

    configure_logging()

    trace_path = os.getenv("VERIDIAN_TRACE_PATH")
    if trace_path and JsonlTraceHook.id not in existing_ids:
        try:
            registry.register(JsonlTraceHook(trace_path))
            attached.append(JsonlTraceHook.id)
        except OSError as exc:
            log.warning("observability.trace_hook_failed path=%s err=%s", trace_path, exc)

    webhook = os.getenv("VERIDIAN_ALERT_WEBHOOK")
    if webhook and WebhookAlertHook.id not in existing_ids:
        try:
            registry.register(WebhookAlertHook(webhook))
            attached.append(WebhookAlertHook.id)
        except ValueError as exc:
            log.warning("observability.alert_hook_failed err=%s", exc)

    return attached
