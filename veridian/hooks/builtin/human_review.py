"""
veridian.hooks.builtin.human_review
──────────────────────────────────────
HumanReviewHook — pauses run for tasks that require human approval.
Priority 50.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.core.exceptions import HumanReviewRequired
from veridian.hooks.base import BaseHook

__all__ = ["HumanReviewHook"]

log = logging.getLogger(__name__)


class HumanReviewHook(BaseHook):
    """
    Raises HumanReviewRequired before a task is dispatched when the task's
    metadata contains the configured review_field set to a truthy value.

    Default field: 'requires_human_review'.
    """

    id: ClassVar[str] = "human_review"
    priority: ClassVar[int] = 50

    def __init__(
        self,
        review_field: str = "requires_human_review",
        notify_webhook: str | None = None,
    ) -> None:
        self.review_field = review_field
        self.notify_webhook = notify_webhook or ""

    def before_task(self, event: Any) -> None:
        """Raise HumanReviewRequired if the task metadata flags it."""
        task = getattr(event, "task", None)
        if not task:
            return
        metadata: dict[str, Any] = getattr(task, "metadata", {}) or {}
        if metadata.get(self.review_field):
            log.info(
                "human_review.required task_id=%s field=%s",
                getattr(task, "id", "?"),
                self.review_field,
            )
            raise HumanReviewRequired(
                task_id=getattr(task, "id", "?"),
                reason=f"Task metadata flagged '{self.review_field}': True",
            )
