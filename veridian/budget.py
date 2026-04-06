"""
veridian.budget
───────────────
A7: First-class Budget type with token, cost, and wall-clock limits.

Usage::

    from veridian.budget import Budget, BudgetState

    budget = Budget(
        token_limit=50_000,
        cost_limit_usd=1.00,
        wall_clock_limit_seconds=300.0,
    )
    state = BudgetState(budget=budget)

    # After each LLM call:
    state.consume(tokens=1500, cost_usd=0.045)
    state.check()  # raises BudgetExceeded if any limit is breached

    # Read remaining capacity:
    print(f"Remaining tokens: {state.remaining_tokens}")
    print(f"Remaining cost:   ${state.remaining_cost_usd:.4f}")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from veridian.core.exceptions import BudgetExceeded

__all__ = ["Budget", "BudgetState"]


# ── Budget ────────────────────────────────────────────────────────────────────


@dataclass
class Budget:
    """
    Immutable limit configuration for a single run or task.

    Any limit set to ``None`` is unconstrained.

    Attributes
    ----------
    token_limit:
        Maximum total tokens (input + output) across all LLM calls.
    cost_limit_usd:
        Maximum total cost in USD.
    wall_clock_limit_seconds:
        Maximum elapsed wall-clock time in seconds from ``BudgetState``
        creation.
    """

    token_limit: int | None = None
    cost_limit_usd: float | None = None
    wall_clock_limit_seconds: float | None = None


# ── BudgetState ───────────────────────────────────────────────────────────────


class BudgetState:
    """
    Mutable tracking of consumption against a ``Budget``.

    Call ``consume()`` after each LLM call, then ``check()`` to raise
    ``BudgetExceeded`` if any limit has been breached.

    Usage::

        state = BudgetState(budget=Budget(token_limit=10000, cost_limit_usd=0.50))
        state.consume(tokens=1500, cost_usd=0.045)
        state.check()  # raises BudgetExceeded if over limit
    """

    def __init__(self, budget: Budget) -> None:
        """Initialise state; records start time for wall-clock tracking."""
        self.budget = budget
        self.tokens_used: int = 0
        self.cost_used_usd: float = 0.0
        self._start_time: float = time.monotonic()

    def consume(self, tokens: int = 0, cost_usd: float = 0.0) -> None:
        """Accumulate usage.  Does NOT raise; call ``check()`` to enforce."""
        self.tokens_used += tokens
        self.cost_used_usd += cost_usd

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since this BudgetState was created."""
        return time.monotonic() - self._start_time

    @property
    def remaining_tokens(self) -> int | None:
        """Tokens remaining, or ``None`` if no token limit is set."""
        if self.budget.token_limit is None:
            return None
        return max(0, self.budget.token_limit - self.tokens_used)

    @property
    def remaining_cost_usd(self) -> float | None:
        """Cost remaining in USD, or ``None`` if no cost limit is set."""
        if self.budget.cost_limit_usd is None:
            return None
        return max(0.0, self.budget.cost_limit_usd - self.cost_used_usd)

    def is_exceeded(self) -> bool:
        """Return ``True`` if ANY limit has been exceeded (no exception raised)."""
        return (
            (self.budget.token_limit is not None and self.tokens_used > self.budget.token_limit)
            or (
                self.budget.cost_limit_usd is not None
                and self.cost_used_usd > self.budget.cost_limit_usd
            )
            or (
                self.budget.wall_clock_limit_seconds is not None
                and self.elapsed_seconds > self.budget.wall_clock_limit_seconds
            )
        )

    def check(self) -> None:
        """Raise ``BudgetExceeded`` if any limit has been exceeded."""
        if self.budget.token_limit is not None and self.tokens_used > self.budget.token_limit:
            raise BudgetExceeded(
                limit_type="tokens",
                current=float(self.tokens_used),
                limit=float(self.budget.token_limit),
            )

        if (
            self.budget.cost_limit_usd is not None
            and self.cost_used_usd > self.budget.cost_limit_usd
        ):
            raise BudgetExceeded(
                limit_type="cost_usd",
                current=self.cost_used_usd,
                limit=self.budget.cost_limit_usd,
            )

        elapsed = self.elapsed_seconds
        if (
            self.budget.wall_clock_limit_seconds is not None
            and elapsed > self.budget.wall_clock_limit_seconds
        ):
            raise BudgetExceeded(
                limit_type="wall_clock_seconds",
                current=elapsed,
                limit=self.budget.wall_clock_limit_seconds,
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize current state for logging/tracing."""
        return {
            "tokens_used": self.tokens_used,
            "cost_used_usd": self.cost_used_usd,
            "elapsed_seconds": self.elapsed_seconds,
            "remaining_tokens": self.remaining_tokens,
            "remaining_cost_usd": self.remaining_cost_usd,
            "is_exceeded": self.is_exceeded(),
        }
