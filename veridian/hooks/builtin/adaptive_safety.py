"""
veridian.hooks.builtin.adaptive_safety
──────────────────────────────────────
AdaptiveSafetyHook — trust-based verification scaling.

Addresses the Impossible Trilemma: safety vs capability vs efficiency.

Trust levels:
  HIGH (1000+)  -> Relaxed (schema + grounding)
  MEDIUM (100+) -> Standard (+ consistency + canary)
  LOW (< 100)   -> Strict (all verifiers + human review)
  NEW (0)       -> Maximum (all + sandbox + human review)

Key invariants:
  - Safety regression -> instant trust reset (ratchet down is instant)
  - Ratchet up is slow (Bayesian — requires statistical evidence)
  - Safety is the optimization target, not the tax
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook

__all__ = [
    "AdaptiveSafetyHook",
    "TrustLevel",
    "TrustScore",
    "VerificationLevel",
]

log = logging.getLogger(__name__)


class TrustLevel(enum.Enum):
    """Agent trust level derived from cumulative trust score."""

    NEW = "new"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationLevel(enum.Enum):
    """Verification intensity mapped from trust level."""

    RELAXED = "relaxed"
    STANDARD = "standard"
    STRICT = "strict"
    MAXIMUM = "maximum"

    @classmethod
    def for_trust(cls, level: TrustLevel) -> VerificationLevel:
        """Map trust level to verification intensity."""
        mapping = {
            TrustLevel.HIGH: cls.RELAXED,
            TrustLevel.MEDIUM: cls.STANDARD,
            TrustLevel.LOW: cls.STRICT,
            TrustLevel.NEW: cls.MAXIMUM,
        }
        return mapping[level]


@dataclass
class TrustScore:
    """Bayesian trust score with instant ratchet-down on safety failure.

    Score accumulates slowly on success, resets to 0 on any safety failure.
    """

    score: int = 0

    @property
    def level(self) -> TrustLevel:
        """Current trust level derived from raw score."""
        if self.score >= 1000:
            return TrustLevel.HIGH
        if self.score >= 100:
            return TrustLevel.MEDIUM
        if self.score > 0:
            return TrustLevel.LOW
        return TrustLevel.NEW

    def record_success(self) -> None:
        """Slow ratchet up: +5 per success (Bayesian evidence accumulation)."""
        self.score += 5

    def record_safety_failure(self) -> None:
        """Instant ratchet down: reset to 0."""
        self.score = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "score": self.score,
            "level": self.level.value,
        }


class AdaptiveSafetyHook(BaseHook):
    """Trust-based verification scaling.

    Tracks a cumulative TrustScore. Exposes get_verification_level()
    for runner integration. Read-only: never mutates ledger.
    """

    id: ClassVar[str] = "adaptive_safety"
    priority: ClassVar[int] = 45  # before CostGuard (50), after IdentityGuard (5)

    def __init__(self, initial_score: int = 0) -> None:
        self._trust = TrustScore(score=initial_score)

    def before_run(self, event: Any) -> None:
        """Log current trust level at run start."""
        log.info(
            "adaptive_safety: trust_score=%d level=%s",
            self._trust.score,
            self._trust.level.value,
        )

    def after_task(self, event: Any) -> None:
        """Record successful task completion."""
        self._trust.record_success()

    def on_failure(self, event: Any) -> None:
        """Check for safety violations and reset trust if found."""
        task = getattr(event, "task", None)
        if task is None:
            return
        metadata = getattr(task, "metadata", {}) or {}
        error = getattr(event, "error", "")

        is_safety = (
            metadata.get("safety_violation")
            or "safety" in str(error).lower()
            or "injection" in str(error).lower()
            or "blocked" in str(error).lower()
        )

        if is_safety:
            log.warning("adaptive_safety: safety failure detected, resetting trust to 0")
            self._trust.record_safety_failure()

    def get_verification_level(self) -> VerificationLevel:
        """Return the current verification level based on trust score."""
        return VerificationLevel.for_trust(self._trust.level)
