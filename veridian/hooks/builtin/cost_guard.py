"""
veridian.hooks.builtin.cost_guard
───────────────────────────────────
CostGuardHook — halts the run when cumulative token cost exceeds max_cost_usd.
Priority 50.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.core.exceptions import CostLimitExceeded
from veridian.hooks.base import BaseHook

__all__ = ["CostGuardHook"]

log = logging.getLogger(__name__)

# Approximate cost per token (USD). Overridable per instance.
_DEFAULT_COST_PER_TOKEN = 0.000_003  # ~$3 / million tokens


class CostGuardHook(BaseHook):
    """
    Tracks cumulative token cost and raises CostLimitExceeded before the next
    task if the budget has already been exhausted.

    Cost is accumulated in after_task() from task.result.token_usage.
    """

    id: ClassVar[str] = "cost_guard"
    priority: ClassVar[int] = 50

    def __init__(
        self,
        max_cost_usd: float = 10.0,
        cost_per_token: float = _DEFAULT_COST_PER_TOKEN,
        warn_at_pct: float = 0.8,
    ) -> None:
        self.max_cost_usd = max_cost_usd
        self.cost_per_token = cost_per_token
        self.warn_at_pct = warn_at_pct
        self._current_cost: float = 0.0

    def before_task(self, event: Any) -> None:
        """Raise CostLimitExceeded if budget is already exhausted."""
        if self._current_cost >= self.max_cost_usd:
            raise CostLimitExceeded(self._current_cost, self.max_cost_usd)

    def after_task(self, event: Any) -> None:
        """Accumulate cost from the completed task's token usage."""
        task = getattr(event, "task", None)
        if not task:
            return
        result = getattr(task, "result", None)
        if not result:
            return
        tokens = result.token_usage.get("total_tokens", 0) if result.token_usage else 0
        self._current_cost += tokens * self.cost_per_token

        if self.max_cost_usd > 0:
            pct = self._current_cost / self.max_cost_usd
            if pct >= self.warn_at_pct:
                log.warning(
                    "cost_guard.warning current=$%.4f limit=$%.2f pct=%.0f%%",
                    self._current_cost, self.max_cost_usd, pct * 100,
                )

    @property
    def current_cost(self) -> float:
        """Return accumulated cost in USD."""
        return self._current_cost
