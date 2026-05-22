"""
veridian.observability.alerts
---------------------------------------------------------------
AlertHook ABC and a webhook-backed reference implementation.

Production deployments need a way to escalate beyond the in-process
``RunSummary.errors`` list. AlertHook fires once per task failure / run
abort and lets operators route to Slack, PagerDuty, opsgenie, etc.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, ClassVar
from urllib import error as urllib_error
from urllib import request as urllib_request

from veridian.hooks.base import BaseHook
from veridian.observability.trace import get_trace_id

__all__ = ["AlertHook", "WebhookAlertHook"]

log = logging.getLogger(__name__)


class AlertHook(BaseHook):
    """Base hook for failure escalation.

    Subclasses implement :meth:`emit` to route ``alert`` payloads to the
    operator's incident system. The default ``on_failure`` / ``after_run``
    wiring builds the payload and delegates to ``emit``.
    """

    id: ClassVar[str] = "alert"
    priority: ClassVar[int] = 90  # run after behaviour hooks; observe final state

    def emit(self, alert: dict[str, Any]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _build_payload(self, kind: str, event: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": kind,
            "run_id": getattr(event, "run_id", ""),
        }
        trace_id = get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        task = getattr(event, "task", None)
        if task is not None:
            payload["task_id"] = getattr(task, "id", "")
            payload["task_title"] = str(getattr(task, "title", ""))[:120]
        for attr in ("error", "last_error", "reason"):
            value = getattr(event, attr, None)
            if value:
                payload[attr] = str(value)[:500]
        return payload

    def on_failure(self, event: Any) -> None:
        try:
            self.emit(self._build_payload("task_failed", event))
        except Exception as exc:
            log.warning("alert.emit_failed kind=task_failed err=%s", exc)

    def after_run(self, event: Any) -> None:
        summary = getattr(event, "summary", None)
        if summary is None:
            return
        if getattr(summary, "failed_count", 0) == 0 and not getattr(summary, "errors", []):
            return
        payload = self._build_payload("run_completed_with_failures", event)
        payload["failed_count"] = getattr(summary, "failed_count", 0)
        payload["done_count"] = getattr(summary, "done_count", 0)
        try:
            self.emit(payload)
        except Exception as exc:
            log.warning("alert.emit_failed kind=run_completed_with_failures err=%s", exc)


class WebhookAlertHook(AlertHook):
    """Post alert payloads to a JSON-accepting HTTP endpoint.

    Uses :mod:`urllib` so the hook has zero non-stdlib dependencies.
    Alerts are sent on a background thread so a slow/down webhook never
    stalls the runner; failures are logged and dropped.
    """

    id: ClassVar[str] = "webhook_alert"

    def __init__(self, url: str, timeout_seconds: float = 5.0) -> None:
        if not url:
            raise ValueError("WebhookAlertHook requires a non-empty url")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def emit(self, alert: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._post, args=(alert,), name="veridian-alert", daemon=True
        )
        thread.start()

    def _post(self, alert: dict[str, Any]) -> None:
        body = json.dumps(alert, default=str).encode("utf-8")
        req = urllib_request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status >= 400:
                    log.warning("alert.webhook_status status=%s url=%s", resp.status, self.url)
        except (urllib_error.URLError, OSError) as exc:
            log.warning("alert.webhook_failed url=%s err=%s", self.url, exc)
