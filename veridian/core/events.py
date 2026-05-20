"""
veridian.core.events
───────────────────
Typed event hierarchy. Every significant lifecycle moment emits one of these.
Hooks receive strongly-typed events — no dict key typos, full IDE autocomplete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass
class VeridianEvent:
    """Base event. All veridian events inherit from this."""

    event_type: str = ""
    run_id: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "ts": self.ts.isoformat(),
            **self.metadata,
        }


# ── Run lifecycle ─────────────────────────────────────────────────────────────


@dataclass
class RunStarted(VeridianEvent):
    event_type: str = "run.started"
    total_tasks: int = 0
    phase: str | None = None


@dataclass
class RunCompleted(VeridianEvent):
    event_type: str = "run.completed"
    summary: Any | None = None  # RunSummary — avoid circular import


@dataclass
class RunAborted(VeridianEvent):
    event_type: str = "run.aborted"
    reason: str = ""


# ── Task lifecycle ────────────────────────────────────────────────────────────


@dataclass
class TaskClaimed(VeridianEvent):
    event_type: str = "task.claimed"
    task: Any | None = None  # Task


@dataclass
class TaskCompleted(VeridianEvent):
    event_type: str = "task.completed"
    task: Any | None = None
    result: Any | None = None  # TaskResult


@dataclass
class TaskFailed(VeridianEvent):
    event_type: str = "task.failed"
    task: Any | None = None
    error: str = ""
    attempt: int = 0


@dataclass
class TaskAbandoned(VeridianEvent):
    event_type: str = "task.abandoned"
    task: Any | None = None
    last_error: str = ""


@dataclass
class TaskSkipped(VeridianEvent):
    event_type: str = "task.skipped"
    task: Any | None = None
    reason: str = ""


# ── Verification ──────────────────────────────────────────────────────────────


@dataclass
class VerificationPassed(VeridianEvent):
    event_type: str = "verification.passed"
    task: Any | None = None
    verifier_id: str = ""
    duration_ms: float = 0.0


@dataclass
class VerificationFailed(VeridianEvent):
    event_type: str = "verification.failed"
    task: Any | None = None
    verifier_id: str = ""
    error: str = ""
    attempt: int = 0
    duration_ms: float = 0.0


# ── Context ───────────────────────────────────────────────────────────────────


# ── Resilience ────────────────────────────────────────────────────────────────


@dataclass
class CircuitBreakerOpened(VeridianEvent):
    event_type: str = "circuit_breaker.opened"
    provider: str = ""
    failure_count: int = 0
    cooldown_seconds: int = 0


@dataclass
class CircuitBreakerClosed(VeridianEvent):
    event_type: str = "circuit_breaker.closed"
    provider: str = ""


@dataclass
class RetryScheduled(VeridianEvent):
    event_type: str = "retry.scheduled"
    task_id: str = ""
    attempt: int = 0
    delay_seconds: float = 0.0
    error_type: str = ""


# ── Cost / rate ───────────────────────────────────────────────────────────────


@dataclass
class CostGuardTriggered(VeridianEvent):
    event_type: str = "cost_guard.triggered"
    current_cost: float = 0.0
    limit: float = 0.0


@dataclass
class CostWarning(VeridianEvent):
    event_type: str = "cost_guard.warning"
    current_cost: float = 0.0
    limit: float = 0.0
    pct: float = 0.0


@dataclass
class RateLimitHit(VeridianEvent):
    event_type: str = "rate_limit.hit"
    retry_after_seconds: float = 0.0


# ── Human review ─────────────────────────────────────────────────────────────


@dataclass
class HumanReviewRequested(VeridianEvent):
    event_type: str = "human_review.requested"
    task: Any | None = None
    reason: str = ""
    notify_webhook: str = ""


@dataclass
class HumanReviewResumed(VeridianEvent):
    event_type: str = "human_review.resumed"
    task_id: str = ""
    approved: bool = True
    reviewer_note: str = ""


# ── Pause / Resume (RV3-001) ─────────────────────────────────────────────────


@dataclass
class TaskPaused(VeridianEvent):
    """Fired when a task transitions IN_PROGRESS → PAUSED via a control-flow
    signal (HumanReviewRequired, TaskPauseRequested)."""

    event_type: str = "task.paused"
    task: Any | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResumed(VeridianEvent):
    """Fired when a runner picks up a PAUSED task and transitions it back to
    IN_PROGRESS. resume_count tracks how many times this task has been resumed."""

    event_type: str = "task.resumed"
    task: Any | None = None
    resume_count: int = 0


# ── SLA ───────────────────────────────────────────────────────────────────────


# ── Drift detection ─────────────────────────────────────────────────────


# ── Sprint Contract ────────────────────────────────────────────────────────────


# ── Adversarial Evaluation ────────────────────────────────────────────────────


# ── Evolution Safety (Phase 7b) ──────────────────────────────────────────────


