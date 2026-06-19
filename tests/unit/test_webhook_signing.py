"""
tests.unit.test_webhook_signing
---------------------------------------------------------------
Fitment follow-up item 4: WebhookAlertHook signs payloads with HMAC-SHA256
when constructed with a ``secret``, and always emits a stable
``idempotency_key`` so receivers can dedupe retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from veridian.core.events import TaskFailed
from veridian.core.task import Task
from veridian.observability.alerts import AlertHook, WebhookAlertHook


def _failed_event(task_id: str = "t1") -> TaskFailed:
    return TaskFailed(run_id="run-42", task=Task(id=task_id, title="trivial"), error="boom")


def test_payload_carries_idempotency_key() -> None:
    class _Capturing(AlertHook):
        id = "capturing"

        def __init__(self) -> None:
            self.alerts: list[dict] = []

        def emit(self, alert: dict) -> None:
            self.alerts.append(alert)

    hook = _Capturing()
    hook.on_failure(_failed_event())
    assert hook.alerts and "idempotency_key" in hook.alerts[0]
    key = hook.alerts[0]["idempotency_key"]
    assert isinstance(key, str) and len(key) >= 16


def test_idempotency_key_is_stable_for_same_event() -> None:
    class _Capturing(AlertHook):
        id = "capturing"

        def __init__(self) -> None:
            self.alerts: list[dict] = []

        def emit(self, alert: dict) -> None:
            self.alerts.append(alert)

    hook = _Capturing()
    hook.on_failure(_failed_event())
    hook.on_failure(_failed_event())
    assert hook.alerts[0]["idempotency_key"] == hook.alerts[1]["idempotency_key"]


def test_webhook_post_includes_idempotency_header() -> None:
    hook = WebhookAlertHook("https://example.invalid/alerts")
    payload = {"kind": "task_failed", "run_id": "r1", "task_id": "t1", "idempotency_key": "abc"}
    with patch("veridian.observability.alerts.urllib_request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        hook._post(payload)
    req = urlopen.call_args.args[0]
    assert req.get_header("X-idempotency-key") == "abc"
    # No signature header without a secret
    assert req.get_header("X-veridian-signature") is None


def test_webhook_signs_payload_when_secret_provided() -> None:
    secret = "shhh"
    hook = WebhookAlertHook("https://example.invalid/alerts", secret=secret)
    payload = {"kind": "task_failed", "run_id": "r1", "task_id": "t1", "idempotency_key": "abc"}
    with patch("veridian.observability.alerts.urllib_request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        hook._post(payload)
    req = urlopen.call_args.args[0]
    body = req.data
    header = req.get_header("X-veridian-signature")
    assert header is not None and header.startswith("sha256=")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert header == f"sha256={expected}"
    # Body must be valid JSON unchanged by signing
    assert json.loads(body.decode("utf-8"))["task_id"] == "t1"


def test_empty_secret_is_ignored() -> None:
    """Empty-string secret is treated as no secret, not as an HMAC over no key."""
    hook = WebhookAlertHook("https://example.invalid/alerts", secret="")
    assert hook._secret_bytes is None


def test_auto_register_reads_webhook_secret_env(tmp_path, monkeypatch) -> None:
    from veridian.hooks.registry import HookRegistry
    from veridian.observability.setup import auto_register

    monkeypatch.delenv("VERIDIAN_LOG_FORMAT", raising=False)
    monkeypatch.delenv("VERIDIAN_TRACE_PATH", raising=False)
    monkeypatch.setenv("VERIDIAN_ALERT_WEBHOOK", "https://example.invalid/hook")
    monkeypatch.setenv("VERIDIAN_ALERT_WEBHOOK_SECRET", "topsecret")

    registry = HookRegistry()
    auto_register(registry)
    webhook_hooks = [h for h in registry.hooks if isinstance(h, WebhookAlertHook)]
    assert webhook_hooks, "WebhookAlertHook should be registered"
    assert webhook_hooks[0]._secret_bytes == b"topsecret"
