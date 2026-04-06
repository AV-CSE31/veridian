"""
veridian.agents.reviewer
──────────────────────────
ReviewerAgent — performs a lightweight quality check on a completed TaskResult
before it is submitted for final verification.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.agents.base import BaseAgent
from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult
from veridian.providers.base import LLMProvider

__all__ = ["ReviewerAgent"]

log = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    """
    Reviews a TaskResult for quality before the verifier runs.
    Returns the result unchanged (or enriched) for the verifier to process.
    """

    id: ClassVar[str] = "reviewer"

    def __init__(
        self,
        provider: LLMProvider,
        config: VeridianConfig,
    ) -> None:
        self.provider = provider
        self.config = config

    def run(self, task: Task, result: TaskResult, **kwargs: Any) -> TaskResult:
        """
        Perform a lightweight review of the result.
        Currently returns the result as-is; can be extended to call the LLM
        for a secondary quality check.
        """
        if not result.structured:
            log.debug(
                "reviewer.empty_structured task_id=%s — result has no structured fields",
                task.id,
            )
        return result
