"""Tests for the opt-in observability package."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from veridian.core.events import RunCompleted, RunStarted, TaskFailed
from veridian.hooks.registry import HookRegistry
from veridian.observability.alerts import AlertHook, WebhookAlertHook
from veridian.observability.logging import JsonLogFormatter, configure_logging
from veridian.observability.setup import auto_register
from veridian.observability.trace import (
    JsonlTraceHook,
    get_trace_id,
    set_trace_id,
)


class _FakeTask:
    def __init__(self, task_id: str = "task-1", title: str = "demo") -> None:
        self.id = task_id
        self.title = title


class _FakeSummary:
    def __init__(self, failed_count: int = 0, errors: list[str] | None = None) -> None:
        self.failed_count = failed_count
        self.done_count = 0
        self.errors = errors or []


# ------ trace id ------------------------------------------------------------


def test_set_and_get_trace_id() -> None:
    set_trace_id("abc123")
    assert get_trace_id() == "abc123"
    set_trace_id(None)
    assert get_trace_id() is not None  # uuid4 fallback


# ------ JSON logging --------------------------------------------------------


def test_json_log_formatter_emits_required_fields() -> None:
    set_trace_id("trace-xyz")
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="veridian.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.task_id = "task-1"
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "veridian.test"
    assert payload["message"] == "hello world"
    assert payload["trace_id"] == "trace-xyz"
    assert payload["task_id"] == "task-1"
    assert "ts" in payload


def test_configure_logging_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIDIAN_LOG_FORMAT", raising=False)
    assert configure_logging() is False

    monkeypatch.setenv("VERIDIAN_LOG_FORMAT", "json")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert configure_logging() is True
        added = [h for h in root.handlers if h not in before]
        assert added, "expected a new StreamHandler to be installed"
        assert isinstance(added[-1].formatter, JsonLogFormatter)
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)


# ------ JsonlTraceHook ------------------------------------------------------


def test_jsonl_trace_hook_writes_one_record_per_event(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    set_trace_id("trace-1")
    hook = JsonlTraceHook(trace_path)
    try:
        hook.before_run(RunStarted(run_id="r1", total_tasks=2))
        hook.on_failure(TaskFailed(run_id="r1", task=_FakeTask(), error="boom"))
    finally:
        hook.close()

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["hook_method"] == "before_run"
    assert first["event_type"] == "run.started"
    assert first["trace_id"] == "trace-1"
    second = json.loads(lines[1])
    assert second["hook_method"] == "on_failure"
    assert second["event_type"] == "task.failed"


# ------ Alerting ------------------------------------------------------------


class _CapturingAlertHook(AlertHook):
    id = "capture"

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    def emit(self, alert: dict[str, Any]) -> None:
        self.alerts.append(alert)


def test_alert_hook_fires_on_task_failure() -> None:
    hook = _CapturingAlertHook()
    hook.on_failure(TaskFailed(run_id="r1", task=_FakeTask("t1"), error="oops"))
    assert len(hook.alerts) == 1
    alert = hook.alerts[0]
    assert alert["kind"] == "task_failed"
    assert alert["task_id"] == "t1"
    assert alert["error"] == "oops"


def test_alert_hook_skips_clean_run() -> None:
    hook = _CapturingAlertHook()
    hook.after_run(RunCompleted(run_id="r1", summary=_FakeSummary(failed_count=0)))
    assert hook.alerts == []


def test_alert_hook_fires_when_run_has_failures() -> None:
    hook = _CapturingAlertHook()
    hook.after_run(RunCompleted(run_id="r1", summary=_FakeSummary(failed_count=2)))
    assert len(hook.alerts) == 1
    assert hook.alerts[0]["kind"] == "run_completed_with_failures"
    assert hook.alerts[0]["failed_count"] == 2


def test_webhook_alert_hook_rejects_empty_url() -> None:
    with pytest.raises(ValueError):
        WebhookAlertHook("")


def test_webhook_alert_hook_posts_payload() -> None:
    hook = WebhookAlertHook("https://example.invalid/alerts")
    with patch("veridian.observability.alerts.urllib_request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        hook._post({"kind": "task_failed", "task_id": "t1"})
    assert urlopen.called
    request_arg = urlopen.call_args.args[0]
    body = json.loads(request_arg.data.decode("utf-8"))
    assert body["task_id"] == "t1"
    assert request_arg.get_header("Content-type") == "application/json"


# ------ auto_register -------------------------------------------------------


def test_auto_register_attaches_hooks_per_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VERIDIAN_LOG_FORMAT", raising=False)
    monkeypatch.setenv("VERIDIAN_TRACE_PATH", str(tmp_path / "trace.jsonl"))
    monkeypatch.setenv("VERIDIAN_ALERT_WEBHOOK", "https://example.invalid/hook")

    registry = HookRegistry()
    attached = auto_register(registry)
    try:
        assert JsonlTraceHook.id in attached
        assert WebhookAlertHook.id in attached
        ids = {getattr(h, "id", "") for h in registry.hooks}
        assert {JsonlTraceHook.id, WebhookAlertHook.id}.issubset(ids)
    finally:
        for hook in registry.hooks:
            close = getattr(hook, "close", None)
            if callable(close):
                close()


def test_auto_register_no_env_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("VERIDIAN_LOG_FORMAT", "VERIDIAN_TRACE_PATH", "VERIDIAN_ALERT_WEBHOOK"):
        monkeypatch.delenv(key, raising=False)
    registry = HookRegistry()
    assert auto_register(registry) == []
    assert registry.hooks == []
