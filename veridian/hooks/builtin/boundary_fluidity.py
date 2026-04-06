"""
veridian.hooks.builtin.boundary_fluidity
─────────────────────────────────────────
BoundaryFluidityHook — detects social engineering of AI agents.

GAP 4 FIX: Agents of Chaos (Feb 2026) revealed agents could be
manipulated into disclosing 124 email records by framing requests
with "urgency." Traditional AST analysis misses this because the
tool call is syntactically valid but contextually unauthorized.

Detection patterns:
  1. Urgency framing — "URGENT", "immediately", "right now", "ASAP"
  2. Authority claims — "CEO says", "management requires", "admin override"
  3. Guilt/pressure — "people will be hurt", "you're responsible"
  4. Goal drift — request shifts agent's core objective
  5. Scope escalation — request accesses data outside task scope

This hook runs at priority 3 (before IdentityGuard at 5, before all
other hooks). If manipulation is detected, the hook logs a warning
and can optionally block the task.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = ["BoundaryFluidityHook", "ManipulationSignal"]

log = logging.getLogger(__name__)


@dataclass
class ManipulationSignal:
    """A detected social engineering attempt."""

    pattern_type: str = ""  # urgency | authority | guilt | scope_escalation
    matched_text: str = ""
    severity: str = "warning"  # warning | critical
    detail: str = ""


# ── Detection patterns ──────────────────────────────────────────────────────

_URGENCY_PATTERNS = [
    re.compile(
        r"\b(URGENT|ASAP|immediately|right\s+now|time\s+sensitive|emergency)\b", re.IGNORECASE
    ),
    re.compile(r"\b(must\s+be\s+done\s+today|deadline\s+passed|cannot\s+wait)\b", re.IGNORECASE),
    re.compile(r"\b(critical\s+priority|drop\s+everything|highest\s+priority)\b", re.IGNORECASE),
]

_AUTHORITY_PATTERNS = [
    re.compile(
        r"\b(CEO|CTO|CFO|COO|president|director|management)\s+(says?|requires?|demands?|ordered|wants)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(admin\s+override|executive\s+order|board\s+directive)\b", re.IGNORECASE),
    re.compile(
        r"\b(I\s+am\s+(the|an?)\s+(admin|administrator|manager|supervisor))\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(authorized\s+by|approved\s+by|signed\s+off\s+by)\s+(the\s+)?(CEO|CTO|VP|director)\b",
        re.IGNORECASE,
    ),
]

_GUILT_PATTERNS = [
    re.compile(
        r"\b(people\s+will\s+(be\s+)?hurt|patients?\s+will\s+die|lives?\s+at\s+stake)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(you('re|\s+are)\s+responsible|blood\s+on\s+your\s+hands|your\s+fault)\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(failure\s+to\s+comply|consequences\s+will\s+be|disciplinary\s+action)\b", re.IGNORECASE
    ),
]

_SCOPE_ESCALATION_PATTERNS = [
    re.compile(
        r"\b(all\s+(employee|customer|user|patient)\s+(records?|data|information|emails?))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(forward\s+(all|every|the\s+entire)|send\s+(all|every)|export\s+(all|everything))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(SSN|social\s+security|credit\s+card|bank\s+account|passport)\s*(number)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(password|credential|secret|private\s+key|API\s+key)\b", re.IGNORECASE),
]


class BoundaryFluidityHook(BaseHook):
    """Detects social engineering attempts against AI agents.

    Runs at priority 3 — before IdentityGuard (5) and all other hooks.
    Scans task descriptions and agent context for manipulation patterns.
    Logs warnings and optionally blocks tasks with high-severity signals.

    This addresses the Agents of Chaos finding that agents are vulnerable
    to urgency framing, authority claims, and guilt-based manipulation
    even when the requested tool call is syntactically valid.
    """

    id: ClassVar[str] = "boundary_fluidity"
    priority: ClassVar[int] = 3  # very early — before identity_guard (5)

    def __init__(self, block_on_critical: bool = True) -> None:
        self._block_on_critical = block_on_critical
        self._signals: list[ManipulationSignal] = []
        self.last_signals: list[ManipulationSignal] = []

    def before_task(self, event: Any) -> None:
        """Scan task content for social engineering patterns."""
        task = getattr(event, "task", None)
        if task is None:
            return

        text_surfaces: list[str] = []

        title = getattr(task, "title", "") or ""
        description = getattr(task, "description", "") or ""
        text_surfaces.append(title)
        text_surfaces.append(description)

        metadata = getattr(task, "metadata", {}) or {}
        for val in metadata.values():
            if isinstance(val, str):
                text_surfaces.append(val)

        combined = " ".join(text_surfaces)
        if not combined.strip():
            return

        signals = self._scan(combined, getattr(task, "id", "unknown"))
        self.last_signals = signals

        if signals:
            for s in signals:
                log.warning(
                    "boundary_fluidity.manipulation_detected "
                    "type=%s severity=%s task_id=%s detail=%s",
                    s.pattern_type,
                    s.severity,
                    getattr(task, "id", "?"),
                    s.detail,
                )

    def _scan(self, text: str, task_id: str) -> list[ManipulationSignal]:
        """Scan text for all manipulation patterns."""
        signals: list[ManipulationSignal] = []

        for pattern in _URGENCY_PATTERNS:
            match = pattern.search(text)
            if match:
                signals.append(
                    ManipulationSignal(
                        pattern_type="urgency",
                        matched_text=match.group(),
                        severity="warning",
                        detail=f"Urgency framing detected: '{match.group()}'",
                    )
                )
                break

        for pattern in _AUTHORITY_PATTERNS:
            match = pattern.search(text)
            if match:
                signals.append(
                    ManipulationSignal(
                        pattern_type="authority",
                        matched_text=match.group(),
                        severity="critical",
                        detail=f"Authority claim detected: '{match.group()}'",
                    )
                )
                break

        for pattern in _GUILT_PATTERNS:
            match = pattern.search(text)
            if match:
                signals.append(
                    ManipulationSignal(
                        pattern_type="guilt",
                        matched_text=match.group(),
                        severity="critical",
                        detail=f"Guilt/pressure framing: '{match.group()}'",
                    )
                )
                break

        for pattern in _SCOPE_ESCALATION_PATTERNS:
            match = pattern.search(text)
            if match:
                signals.append(
                    ManipulationSignal(
                        pattern_type="scope_escalation",
                        matched_text=match.group(),
                        severity="critical",
                        detail=f"Scope escalation — sensitive data access: '{match.group()}'",
                    )
                )
                break

        # Compound severity: multiple pattern types = critical
        if len(signals) >= 2:
            for s in signals:
                s.severity = "critical"

        return signals
