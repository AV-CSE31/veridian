"""
veridian.hooks.builtin.slack
──────────────────────────────
SlackNotifyHook — posts run lifecycle events to a Slack incoming webhook.
Priority 50.  No-ops silently when webhook_url is not configured.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = ["SlackNotifyHook"]

log = logging.getLogger(__name__)


class SlackNotifyHook(BaseHook):
    """
    Posts run and task lifecycle events to a Slack incoming webhook URL.
    Silently skips posting when webhook_url is empty.
    """

    id: ClassVar[str] = "slack_notify"
    priority: ClassVar[int] = 50

    def __init__(
        self,
        webhook_url: str | None = None,
        notify_on: list[str] | None = None,
    ) -> None:
        self.webhook_url = webhook_url or ""
        self.notify_on = set(notify_on or ["after_run", "on_failure"])

    def before_run(self, event: Any) -> None:
        if "before_run" in self.notify_on:
            run_id = getattr(event, "run_id", "")
            self._post(f":rocket: Veridian run started — `{run_id}`")

    def after_run(self, event: Any) -> None:
        if "after_run" in self.notify_on:
            run_id = getattr(event, "run_id", "")
            summary = getattr(event, "summary", None)
            if summary:
                done = getattr(summary, "done_count", "?")
                failed = getattr(summary, "failed_count", "?")
                self._post(
                    f":white_check_mark: Run `{run_id}` complete — {done} done, {failed} failed"
                )
            else:
                self._post(f":white_check_mark: Run `{run_id}` complete")

    def on_failure(self, event: Any) -> None:
        if "on_failure" in self.notify_on:
            task = getattr(event, "task", None)
            error = getattr(event, "error", "") or getattr(event, "last_error", "")
            task_id = getattr(task, "id", "?") if task else "?"
            self._post(f":x: Task `{task_id}` failed — {str(error)[:200]}")

    def _post(self, text: str) -> None:
        """POST message to Slack webhook. Silently swallows errors."""
        if not self.webhook_url:
            return
        try:
            import httpx  # noqa: PLC0415

            httpx.post(
                self.webhook_url,
                json={"text": text},
                timeout=5.0,
            )
        except Exception as exc:
            log.debug("slack_notify.post_failed url=%s err=%s", self.webhook_url, exc)
