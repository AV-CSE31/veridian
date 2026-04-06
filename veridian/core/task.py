"""
veridian.core.task
─────────────────
Core domain models. Zero external dependencies.
These are the only objects that travel through the entire system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

# ── STATUS & PRIORITY ────────────────────────────────────────────────────────

# Valid state machine transitions — kept outside the Enum to avoid being
# treated as an enum member (a Python enum ClassVar limitation in 3.11).
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "skipped"},
    # RV3-001: IN_PROGRESS can transition to PAUSED when a control-flow hook
    # (HumanReviewHook, TaskPauseRequested) requests pause during execution.
    "in_progress": {"verifying", "failed", "pending", "paused", "skipped"},
    # RV3-001: PAUSED resumes via IN_PROGRESS (never directly to PENDING — that
    # would destroy the pause payload). Can also fail/abandon on timeout.
    "paused": {"in_progress", "failed", "abandoned"},
    "verifying": {"done", "failed"},
    "failed": {"pending", "abandoned"},  # pending = retry
    "done": set(),  # terminal
    "abandoned": set(),  # terminal
    "skipped": set(),  # terminal
}


class TaskStatus(StrEnum):
    """
    Task lifecycle. The ledger is the only object allowed to transition status.
    All transitions are validated at write time against _VALID_TRANSITIONS.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"
    SKIPPED = "skipped"
    PAUSED = "paused"  # RV3-001: HITL / control-flow pause, resumable

    def can_transition_to(self, new: TaskStatus) -> bool:
        """Return True if transitioning to new status is valid."""
        return new.value in _VALID_TRANSITIONS.get(self.value, set())

    @property
    def is_terminal(self) -> bool:
        """Return True if this status is a terminal (final) state."""
        return self in {TaskStatus.DONE, TaskStatus.ABANDONED, TaskStatus.SKIPPED}


class TaskPriority(int, Enum):
    """Convenience constants. Any int 0–100 is valid."""

    CRITICAL = 100
    HIGH = 75
    NORMAL = 50
    LOW = 25
    DEFERRED = 0


# ── PRM EVIDENCE TYPES ────────────────────────────────────────────────────────


@dataclass
class TraceStep:
    """Single reasoning/process step for PRM and replay analysis."""

    step_id: str
    role: str
    action_type: str
    content: str
    timestamp_ms: int
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: Any = None
    token_count: int | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "role": self.role,
            "action_type": self.action_type,
            "content": self.content,
            "timestamp_ms": self.timestamp_ms,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "token_count": self.token_count,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceStep:
        return cls(
            step_id=str(d.get("step_id", "")),
            role=str(d.get("role", "")),
            action_type=str(d.get("action_type", "")),
            content=str(d.get("content", "")),
            timestamp_ms=int(d.get("timestamp_ms", 0)),
            tool_name=d.get("tool_name"),
            tool_args=d.get("tool_args"),
            tool_result=d.get("tool_result"),
            token_count=d.get("token_count"),
            latency_ms=d.get("latency_ms"),
            metadata=d.get("metadata", {}) or {},
        )


@dataclass
class PRMScore:
    """Step-level PRM scoring output."""

    step_id: str
    score: float
    confidence: float
    model_id: str
    version: str
    failure_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "score": self.score,
            "confidence": self.confidence,
            "model_id": self.model_id,
            "version": self.version,
            "failure_mode": self.failure_mode,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PRMScore:
        return cls(
            step_id=str(d.get("step_id", "")),
            score=float(d.get("score", 0.0)),
            confidence=float(d.get("confidence", 0.0)),
            model_id=str(d.get("model_id", "")),
            version=str(d.get("version", "")),
            failure_mode=d.get("failure_mode"),
        )


@dataclass
class PRMRunResult:
    """Aggregate PRM result for a task/run segment."""

    passed: bool
    aggregate_score: float
    aggregate_confidence: float
    threshold: float
    scored_steps: list[PRMScore] = field(default_factory=list)
    policy_action: str = "allow"
    repair_hint: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "aggregate_score": self.aggregate_score,
            "aggregate_confidence": self.aggregate_confidence,
            "threshold": self.threshold,
            "scored_steps": [s.to_dict() for s in self.scored_steps],
            "policy_action": self.policy_action,
            "repair_hint": self.repair_hint,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PRMRunResult:
        scored = d.get("scored_steps", []) or []
        return cls(
            passed=bool(d.get("passed", False)),
            aggregate_score=float(d.get("aggregate_score", 0.0)),
            aggregate_confidence=float(d.get("aggregate_confidence", 0.0)),
            threshold=float(d.get("threshold", 0.0)),
            scored_steps=[PRMScore.from_dict(s) for s in scored if isinstance(s, dict)],
            policy_action=str(d.get("policy_action", "allow")),
            repair_hint=d.get("repair_hint"),
            error=d.get("error"),
        )


@dataclass
class PRMBudget:
    """PRM runtime budget controls."""

    max_steps_per_call: int = 0
    max_tokens_per_call: int = 0
    max_latency_ms: int = 0
    max_total_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps_per_call": self.max_steps_per_call,
            "max_tokens_per_call": self.max_tokens_per_call,
            "max_latency_ms": self.max_latency_ms,
            "max_total_cost_usd": self.max_total_cost_usd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PRMBudget:
        return cls(
            max_steps_per_call=int(d.get("max_steps_per_call", 0)),
            max_tokens_per_call=int(d.get("max_tokens_per_call", 0)),
            max_latency_ms=int(d.get("max_latency_ms", 0)),
            max_total_cost_usd=float(d.get("max_total_cost_usd", 0.0)),
        )


# ── RESULT ───────────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    """
    Evidence produced by the agent after completing a task.
    Must satisfy the task's verifier contract to trigger DONE.
    """

    raw_output: str  # full LLM response
    structured: dict[str, Any] = field(default_factory=dict)  # parsed claims
    artifacts: list[str] = field(default_factory=list)  # file paths / URLs

    # Bash execution records
    bash_outputs: list[dict[str, Any]] = field(default_factory=list)
    # [{cmd, stdout, stderr, exit_code, duration_ms}]

    # Runtime evidence envelope (consumed by hooks/analytics)
    trace_steps: list[TraceStep] = field(default_factory=list)
    prm_result: PRMRunResult | None = None
    confidence: dict[str, Any] | None = None
    verifier_score: float | None = None
    tool_calls: list[Any] = field(default_factory=list)
    timing: dict[str, Any] = field(default_factory=dict)
    verification_evidence: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    # Verification outcome (set by runner after verifier runs)
    verified: bool = False
    verification_error: str | None = None
    verified_at: datetime | None = None

    # Token accounting (set by provider)
    token_usage: dict[str, int] = field(default_factory=dict)
    # {input_tokens, output_tokens, total_tokens}

    def to_dict(self) -> dict[str, Any]:
        out = {
            "raw_output": self.raw_output,
            "structured": self.structured,
            "artifacts": self.artifacts,
            "bash_outputs": self.bash_outputs,
            "trace_steps": [s.to_dict() for s in self.trace_steps],
            "prm_result": self.prm_result.to_dict() if self.prm_result else None,
            "confidence": self.confidence,
            "verifier_score": self.verifier_score,
            "tool_calls": self.tool_calls,
            "timing": self.timing,
            "verification_evidence": self.verification_evidence,
            "verified": self.verified,
            "verification_error": self.verification_error,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "token_usage": self.token_usage,
        }
        out.update(self.extras)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskResult:
        r = cls(raw_output=d.get("raw_output", ""))
        r.structured = d.get("structured", {})
        r.artifacts = d.get("artifacts", [])
        r.bash_outputs = d.get("bash_outputs", [])
        r.trace_steps = [
            TraceStep.from_dict(s) for s in (d.get("trace_steps", []) or []) if isinstance(s, dict)
        ]
        if isinstance(d.get("prm_result"), dict):
            r.prm_result = PRMRunResult.from_dict(d["prm_result"])
        r.confidence = d.get("confidence")
        r.verifier_score = d.get("verifier_score")
        r.tool_calls = d.get("tool_calls", [])
        r.timing = d.get("timing", {})
        r.verification_evidence = d.get("verification_evidence", {})
        r.verified = d.get("verified", False)
        r.verification_error = d.get("verification_error")
        r.token_usage = d.get("token_usage", {})
        if d.get("verified_at"):
            r.verified_at = datetime.fromisoformat(d["verified_at"])
        known = {
            "raw_output",
            "structured",
            "artifacts",
            "bash_outputs",
            "trace_steps",
            "prm_result",
            "confidence",
            "verifier_score",
            "tool_calls",
            "timing",
            "verification_evidence",
            "verified",
            "verification_error",
            "verified_at",
            "token_usage",
        }
        r.extras = {k: v for k, v in d.items() if k not in known}
        return r


# ── TASK ─────────────────────────────────────────────────────────────────────


@dataclass
class Task:
    """
    The atomic unit of work. Immutable identity; mutable lifecycle.

    KEY DESIGN RULES:
    - description must explain BOTH what to do AND what done looks like
    - verifier_id must exist in the VerifierRegistry at run time
    - depends_on is a list of Task.id strings that must be DONE before this runs
    - metadata is a free-form dict for domain-specific payload (source_file, etc.)
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    description: str = ""

    # ── Scheduling ────────────────────────────────────────────────────────────
    status: TaskStatus = TaskStatus.PENDING
    priority: int = TaskPriority.NORMAL
    phase: str = "default"
    depends_on: list[str] = field(default_factory=list)

    # ── Verification contract ─────────────────────────────────────────────────
    verifier_id: str = "bash_exit"
    verifier_config: dict[str, Any] = field(default_factory=dict)

    # ── Retry state ───────────────────────────────────────────────────────────
    result: TaskResult | None = None
    retry_count: int = 0
    max_retries: int = 3
    last_error: str | None = None  # injected verbatim into next agent prompt

    # ── Ownership (set by runner on claim) ────────────────────────────────────
    claimed_by: str | None = None  # run_id

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # ── Domain payload ────────────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def can_transition_to(self, new_status: TaskStatus) -> bool:
        return self.status.can_transition_to(new_status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority,
            "phase": self.phase,
            "depends_on": self.depends_on,
            "verifier_id": self.verifier_id,
            "verifier_config": self.verifier_config,
            "result": self.result.to_dict() if self.result else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "last_error": self.last_error,
            "claimed_by": self.claimed_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        t = cls(
            id=d["id"],
            title=d.get("title", ""),
            description=d.get("description", ""),
            status=TaskStatus(d.get("status", "pending")),
            priority=d.get("priority", TaskPriority.NORMAL),
            phase=d.get("phase", "default"),
            depends_on=d.get("depends_on", []),
            verifier_id=d.get("verifier_id", "bash_exit"),
            verifier_config=d.get("verifier_config", {}),
            retry_count=d.get("retry_count", 0),
            max_retries=d.get("max_retries", 3),
            last_error=d.get("last_error"),
            claimed_by=d.get("claimed_by"),
            metadata=d.get("metadata", {}),
        )
        if d.get("result"):
            t.result = TaskResult.from_dict(d["result"])
        if d.get("created_at"):
            t.created_at = datetime.fromisoformat(d["created_at"])
        if d.get("updated_at"):
            t.updated_at = datetime.fromisoformat(d["updated_at"])
        return t

    def __repr__(self) -> str:
        return (
            f"Task(id={self.id!r}, title={self.title[:40]!r}, "
            f"status={self.status.value}, priority={self.priority})"
        )


# ── STATS ────────────────────────────────────────────────────────────────────


@dataclass
class LedgerStats:
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    phases: dict[str, int] = field(default_factory=dict)  # phase → pending count
    retry_rate: float = 0.0  # failed / total
    total_tokens_used: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def done(self) -> int:
        return self.by_status.get("done", 0)

    @property
    def pending(self) -> int:
        return self.by_status.get("pending", 0)

    @property
    def failed(self) -> int:
        return self.by_status.get("failed", 0)

    @property
    def in_progress(self) -> int:
        return self.by_status.get("in_progress", 0)

    @property
    def pct_complete(self) -> float:
        if self.total == 0:
            return 0.0
        terminal = self.by_status.get("done", 0) + self.by_status.get("skipped", 0)
        return terminal / self.total
