"""
veridian.loop.checkpoint_cursor
────────────────────────────────
WCP-011: Deterministic per-step checkpoint cursor.

A ``CheckpointCursor`` is the canonical, replay-safe handle to a running
workflow's progress. It pinpoints the exact step that was last fully
persisted so a resumed run knows where to restart without duplicating
side effects.

Design contract:

- ``cursor`` is keyed by ``(task_id, step_index)`` — monotonically
  increasing per task. Two steps with the same index on the same task
  denote the same checkpoint.
- ``step_id`` is the app-level identifier (matches ``TraceStep.step_id``
  when applicable). Redundant with ``step_index`` but preserved for
  operator-facing readability.
- ``activity_key`` is the idempotency key of the most recent activity
  journal entry at the time the cursor was stamped. Resume logic combines
  the cursor with the activity journal to skip cached side effects.
- ``state_hash`` fingerprints the minimal state needed to detect drift
  between cursor advances (e.g. verifier config or model change).
- Cursors are append-only within a task — never rewind; monotonic
  ``step_index`` is an invariant enforced at write time.

Persistence:
- Persisted in ``TaskResult.extras['checkpoint_cursor']`` as a dict.
- Read by :func:`load_cursor` / written by :func:`advance_cursor`.
- Storage is delegated to whichever ``RuntimeStore`` backs the run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from veridian.core.exceptions import VeridianError
from veridian.core.task import TaskResult

__all__ = [
    "CheckpointCursor",
    "CheckpointCursorError",
    "advance_cursor",
    "compute_state_hash",
    "cursor_from_result",
    "is_step_completed",
    "load_cursor",
    "write_cursor",
]


class CheckpointCursorError(VeridianError):
    """Cursor integrity violation (monotonicity, schema, or drift)."""


@dataclass(frozen=True, slots=True)
class CheckpointCursor:
    """Canonical per-step resume cursor.

    Frozen — every advance produces a new cursor rather than mutating in
    place. This keeps the append-only invariant mechanical.

    Fields
    ------
    task_id
        Task the cursor belongs to. Resume logic rejects a cursor loaded
        for a different task_id to avoid cross-task bleed.
    step_index
        Monotonic 0-based index. The first cursor written is ``0``; every
        advance increments by exactly 1.
    step_id
        Application-level step identifier. Matches ``TraceStep.step_id``
        when the cursor is stamped from a trace step; may be any stable
        string otherwise (e.g. ``"verify"``, ``"finalize"``).
    activity_key
        Idempotency key (:class:`~veridian.loop.activity.ActivityRecord`)
        of the most recent activity at cursor time. Empty string when no
        activity has fired yet.
    state_hash
        SHA-256 of the minimal drift-detection payload. Computed via
        :func:`compute_state_hash`. Non-empty only when the caller
        supplied a state dict.
    timestamp_ms
        Wall-clock millis when the cursor was written. Informational
        only — NOT used for ordering.
    metadata
        Arbitrary dict for operator context. Never load-bearing.
    """

    task_id: str
    step_index: int
    step_id: str
    activity_key: str = ""
    state_hash: str = ""
    timestamp_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise CheckpointCursorError("CheckpointCursor.task_id must be non-empty")
        if self.step_index < 0:
            raise CheckpointCursorError(
                f"CheckpointCursor.step_index must be >= 0, got {self.step_index}"
            )
        if not self.step_id:
            raise CheckpointCursorError("CheckpointCursor.step_id must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointCursor:
        return cls(
            task_id=str(data.get("task_id", "")),
            step_index=int(data.get("step_index", 0)),
            step_id=str(data.get("step_id", "")),
            activity_key=str(data.get("activity_key", "")),
            state_hash=str(data.get("state_hash", "")),
            timestamp_ms=int(data.get("timestamp_ms", 0)),
            metadata=dict(data.get("metadata", {}) or {}),
        )


def compute_state_hash(state: dict[str, Any] | None) -> str:
    """Deterministic SHA-256 over a state dict.

    ``None`` / empty returns empty string (no drift check). Non-JSON
    values fall back to ``repr`` so hashing never crashes on arbitrary
    objects.
    """
    if not state:
        return ""
    try:
        serialised = json.dumps(state, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        serialised = repr(state).encode("utf-8")
    return hashlib.sha256(serialised).hexdigest()


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def load_cursor(result: TaskResult | None) -> CheckpointCursor | None:
    """Return the cursor stored in ``result.extras['checkpoint_cursor']`` or
    None. Corruption/schema violations raise ``CheckpointCursorError``."""
    if result is None:
        return None
    raw = result.extras.get("checkpoint_cursor")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CheckpointCursorError(
            f"Invalid checkpoint_cursor payload: expected dict, got {type(raw).__name__}"
        )
    return CheckpointCursor.from_dict(raw)


def write_cursor(result: TaskResult, cursor: CheckpointCursor) -> None:
    """Stamp the cursor onto ``result.extras`` in place. Caller is
    responsible for persisting the TaskResult via the ledger."""
    result.extras["checkpoint_cursor"] = cursor.to_dict()


def advance_cursor(
    *,
    result: TaskResult,
    task_id: str,
    step_id: str,
    activity_key: str = "",
    state: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CheckpointCursor:
    """Produce the next cursor and stamp it onto ``result.extras``.

    Enforces monotonicity: the new cursor's ``step_index`` is exactly one
    greater than the previously stored cursor's, or ``0`` if none exists.
    Task-id mismatch between the previous cursor and the advance call is
    rejected as ``CheckpointCursorError``.
    """
    previous = load_cursor(result)
    if previous is not None:
        if previous.task_id != task_id:
            raise CheckpointCursorError(
                f"Cursor task_id mismatch: stored={previous.task_id!r} incoming={task_id!r}"
            )
        next_index = previous.step_index + 1
    else:
        next_index = 0

    cursor = CheckpointCursor(
        task_id=task_id,
        step_index=next_index,
        step_id=step_id,
        activity_key=activity_key,
        state_hash=compute_state_hash(state),
        timestamp_ms=_now_ms(),
        metadata=dict(metadata or {}),
    )
    write_cursor(result, cursor)
    return cursor


def cursor_from_result(result: TaskResult | None) -> CheckpointCursor | None:
    """Convenience alias for :func:`load_cursor`."""
    return load_cursor(result)


def is_step_completed(result: TaskResult | None, step_id: str) -> bool:
    """Return True if ``step_id`` appears in the trace steps of a result
    that has advanced past it. Used by the runner to decide whether to
    skip an activity on resume.

    The rule: a step is considered completed when (a) the cursor has
    advanced at or beyond that step_id, or (b) an `ActivityRecord` with a
    matching idempotency key is in the journal. This module only answers
    (a); (b) is answered by the ActivityJournal directly.
    """
    cursor = load_cursor(result)
    if cursor is None:
        return False
    if result is None:
        return False
    # The step is completed if its step_id appears in trace_steps AND the
    # current cursor step_index is >= the trace position of that step.
    for idx, step in enumerate(result.trace_steps):
        if step.step_id == step_id:
            return cursor.step_index >= idx
    return False
