"""
veridian.hooks.builtin.logging_hook
─────────────────────────────────────
LoggingHook — structured log lines for every lifecycle event.
Priority 0: runs first among all hooks.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = ["LoggingHook"]

log = logging.getLogger("veridian.run")


class LoggingHook(BaseHook):
    """Emits structured log lines for run and task lifecycle events."""

    id: ClassVar[str] = "logging"
    priority: ClassVar[int] = 0

    def before_run(self, event: Any) -> None:
        """Log run start."""
        run_id = getattr(event, "run_id", "")
        total = getattr(event, "total_tasks", "?")
        log.info("run.started run_id=%s total_tasks=%s", run_id, total)

    def after_run(self, event: Any) -> None:
        """Log run completion."""
        run_id = getattr(event, "run_id", "")
        summary = getattr(event, "summary", None)
        if summary:
            done = getattr(summary, "done_count", "?")
            failed = getattr(summary, "failed_count", "?")
            log.info(
                "run.completed run_id=%s done=%s failed=%s",
                run_id,
                done,
                failed,
            )
        else:
            log.info("run.completed run_id=%s", run_id)

    def before_task(self, event: Any) -> None:
        """Log task dispatch."""
        task = getattr(event, "task", None)
        if task:
            log.info(
                "task.claimed run_id=%s task_id=%s title=%s",
                getattr(event, "run_id", ""),
                getattr(task, "id", "?"),
                str(getattr(task, "title", ""))[:50],
            )

    def after_task(self, event: Any) -> None:
        """Log task completion."""
        task = getattr(event, "task", None)
        if task:
            log.info(
                "task.completed run_id=%s task_id=%s status=%s",
                getattr(event, "run_id", ""),
                getattr(task, "id", "?"),
                getattr(task, "status", "?"),
            )

    def on_failure(self, event: Any) -> None:
        """Log task failure."""
        task = getattr(event, "task", None)
        error = getattr(event, "error", "") or getattr(event, "last_error", "")
        if task:
            log.warning(
                "task.failed run_id=%s task_id=%s error=%s",
                getattr(event, "run_id", ""),
                getattr(task, "id", "?"),
                str(error)[:200],
            )
