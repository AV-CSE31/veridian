"""
veridian.context.window
────────────────────────
TokenWindow — tracks a fixed token budget for context assembly.
Used by ContextManager to decide whether optional blocks fit.
"""
from __future__ import annotations

__all__ = ["TokenWindow"]


class TokenWindow:
    """
    Manages a fixed token budget.

    Usage::

        window = TokenWindow(capacity=8000)
        if window.fits(500):
            window.consume(500)
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"TokenWindow capacity must be > 0, got {capacity}")
        self.capacity = capacity
        self._used: int = 0

    def fits(self, tokens: int) -> bool:
        """Return True if tokens fit within remaining budget."""
        return self._used + tokens <= self.capacity

    def consume(self, tokens: int) -> None:
        """Record tokens as used. Does not enforce the limit."""
        self._used += tokens

    @property
    def used(self) -> int:
        """Tokens consumed so far."""
        return self._used

    @property
    def remaining(self) -> int:
        """Remaining token budget."""
        return max(0, self.capacity - self._used)

    @property
    def pct_used(self) -> float:
        """Fraction of capacity consumed (0.0–1.0+)."""
        return self._used / self.capacity if self.capacity > 0 else 0.0

    def reset(self) -> None:
        """Reset usage counter to zero."""
        self._used = 0
