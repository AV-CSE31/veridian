"""
veridian.context.compactor
────────────────────────────
ContextCompactor — trims the message list when the token budget is near capacity.

RULES (from CLAUDE.md §2.4):
  - Trigger at 85% capacity
  - Never drop: system prompt, last 3 exchanges, current task block
"""

from __future__ import annotations

import logging
from typing import Any

from veridian.context.window import TokenWindow

__all__ = ["ContextCompactor"]

log = logging.getLogger(__name__)

_COMPACTION_THRESHOLD = 0.85  # trigger at 85% capacity
_MIN_KEEP_EXCHANGES = 3  # always keep last 3 exchanges (6 messages)
_SYSTEM_ROLES = {"system"}


class ContextCompactor:
    """
    Compacts a message list to stay within the token budget.

    Preserves:
      - All system messages
      - The first non-system message (task block)
      - The last 3 exchanges (6 messages)

    Drops middle messages when above the 85% threshold.
    """

    def __init__(
        self,
        window: TokenWindow,
        provider: Any | None = None,
    ) -> None:
        self.window = window
        self._provider = provider

    def needs_compaction(self) -> bool:
        """Return True when the token window is ≥ 85% full."""
        return self.window.pct_used >= _COMPACTION_THRESHOLD

    def compact(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """
        Remove middle messages while preserving system, task block, and last
        3 exchanges. Returns the compacted list.
        """
        system_msgs = [m for m in messages if m.get("role") in _SYSTEM_ROLES]
        non_system = [m for m in messages if m.get("role") not in _SYSTEM_ROLES]

        keep_tail = _MIN_KEEP_EXCHANGES * 2  # last 3 exchanges = 6 messages

        # Not enough messages to warrant compaction
        if len(non_system) <= keep_tail + 1:
            return messages

        head = non_system[:1]  # task block
        tail = non_system[-keep_tail:]

        dropped = len(non_system) - len(head) - len(tail)
        if dropped <= 0:
            return messages

        log.info(
            "context.compacted dropped=%d system=%d kept=%d",
            dropped,
            len(system_msgs),
            len(head) + len(tail),
        )
        return system_msgs + head + tail
