"""
veridian.loop.activity
───────────────────────
RV3-004 + RV3-005: side-effect boundary and deterministic activity journal.

Temporal-inspired primitive. Every external call (LLM, tool, file write) that
should be replay-safe is wrapped in ``run_activity``. The ``ActivityJournal``
is an append-only log persisted as ``TaskResult.extras['activity_journal']``.

Contract:
- Each activity has a stable ``idempotency_key`` (either caller-provided or
  deterministically derived from ``fn_name`` + argument hash).
- Before executing, ``run_activity`` checks the journal. If a successful
  record exists for the key, the cached ``result`` is returned — zero
  duplicate LLM calls across restart.
- If no cached success exists, the function is executed with bounded retries
  per ``RetryPolicy``. Every attempt is journaled (success or failure).
- Failed records are NOT replayed as success — a new run re-executes the
  activity so the retry boundary is visible to the runner.

This primitive is deliberately narrow: it does not own serialization or
storage. The runner snapshots the journal into ``TaskResult.extras`` and the
ledger persists it via ``checkpoint_result``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from veridian.core.exceptions import VeridianError

__all__ = [
    "ActivityError",
    "ActivityJournal",
    "ActivityRecord",
    "RetryPolicy",
    "run_activity",
]

log = logging.getLogger(__name__)


class ActivityError(VeridianError):
    """Raised by run_activity when all retry attempts fail.

    Carries the final underlying exception and the number of attempts for
    downstream audit and debugging.
    """

    def __init__(self, fn_name: str, attempts: int, cause: BaseException) -> None:
        self.fn_name = fn_name
        self.attempts = attempts
        self.cause = cause
        super().__init__(f"Activity {fn_name!r} failed after {attempts} attempts: {cause}")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry configuration for ``run_activity``.

    - ``max_attempts``: 1 = no retries, 2 = one retry, ...
    - ``backoff_seconds``: fixed delay between attempts (linear). Deterministic
      replay prefers a constant floor; exponential backoff is intentionally
      avoided here because it complicates replay tests.
    """

    max_attempts: int = 3
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0.0:
            raise ValueError("backoff_seconds must be >= 0.0")


@dataclass
class ActivityRecord:
    """Single journaled activity invocation."""

    activity_id: str
    idempotency_key: str
    fn_name: str
    args_hash: str
    result: Any
    attempts: int
    status: str  # "success" | "failed" | "pending"
    timestamp_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActivityRecord:
        return cls(
            activity_id=str(d.get("activity_id", "")),
            idempotency_key=str(d.get("idempotency_key", "")),
            fn_name=str(d.get("fn_name", "")),
            args_hash=str(d.get("args_hash", "")),
            result=d.get("result"),
            attempts=int(d.get("attempts", 0)),
            status=str(d.get("status", "pending")),
            timestamp_ms=int(d.get("timestamp_ms", 0)),
            error=d.get("error"),
        )


@dataclass
class ActivityJournal:
    """Append-only in-memory journal of activity invocations.

    The journal is keyed by idempotency_key; appending a record with an
    existing key overwrites it (latest-write-wins). This matches the Temporal
    pattern where a retry that transitions pending→success supersedes the
    earlier pending entry.
    """

    records: list[ActivityRecord] = field(default_factory=list)
    _index: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.records)

    def append(self, record: ActivityRecord) -> None:
        if record.idempotency_key in self._index:
            idx = self._index[record.idempotency_key]
            self.records[idx] = record
        else:
            self._index[record.idempotency_key] = len(self.records)
            self.records.append(record)

    def get(self, idempotency_key: str) -> ActivityRecord | None:
        idx = self._index.get(idempotency_key)
        if idx is None:
            return None
        return self.records[idx]

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]]) -> ActivityJournal:
        journal = cls()
        for item in data:
            if isinstance(item, dict):
                journal.append(ActivityRecord.from_dict(item))
        return journal


def _hash_args(args: tuple[Any, ...], kwargs: dict[str, Any] | None = None) -> str:
    """Deterministic SHA-256 hash of (args, kwargs). Uses json default=str so
    non-serializable objects fall back to their repr rather than raising."""
    payload = {"args": list(args), "kwargs": kwargs or {}}
    try:
        serialised = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        serialised = repr(payload).encode("utf-8")
    return hashlib.sha256(serialised).hexdigest()


def _derive_key(fn_name: str, args_hash: str) -> str:
    return f"{fn_name}:{args_hash}"


def run_activity(
    *,
    journal: ActivityJournal,
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    fn_name: str | None = None,
    idempotency_key: str | None = None,
    retry_policy: RetryPolicy | None = None,
) -> Any:
    """Execute a side-effectful function with journal-based replay safety.

    Behavior:
    1. Compute idempotency_key if not provided (derived from fn_name + args_hash).
    2. Look up the journal. If a successful record exists, return its cached
       ``result`` immediately. No execution, no retries, no side effects.
    3. Otherwise, execute ``fn(*args, **kwargs)`` with bounded retries per
       ``retry_policy``. Record the outcome (success or failure) in the journal.
    4. On total failure, raise ``ActivityError`` with the last exception and
       the number of attempts used.
    """
    policy = retry_policy or RetryPolicy()
    kwargs = kwargs or {}
    raw_name = fn_name if fn_name is not None else getattr(fn, "__name__", "anonymous")
    name = raw_name if isinstance(raw_name, str) else str(raw_name)
    args_hash = _hash_args(args, kwargs)
    key = idempotency_key or _derive_key(name, args_hash)

    cached = journal.get(key)
    if cached is not None and cached.status == "success":
        log.debug("activity.cache_hit key=%s fn=%s", key, name)
        return cached.result

    activity_id = f"act_{len(journal) + 1:06d}"
    last_error: BaseException | None = None
    attempts = 0

    for attempt in range(1, policy.max_attempts + 1):
        attempts = attempt
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            log.debug(
                "activity.attempt_failed fn=%s attempt=%d err=%s",
                name,
                attempt,
                exc,
            )
            if attempt < policy.max_attempts and policy.backoff_seconds > 0:
                time.sleep(policy.backoff_seconds)
            continue

        # Success — record and return.
        journal.append(
            ActivityRecord(
                activity_id=activity_id,
                idempotency_key=key,
                fn_name=name,
                args_hash=args_hash,
                result=result,
                attempts=attempt,
                status="success",
                timestamp_ms=int(time.time() * 1000),
            )
        )
        log.debug("activity.success fn=%s attempts=%d", name, attempt)
        return result

    # All attempts exhausted.
    journal.append(
        ActivityRecord(
            activity_id=activity_id,
            idempotency_key=key,
            fn_name=name,
            args_hash=args_hash,
            result=None,
            attempts=attempts,
            status="failed",
            timestamp_ms=int(time.time() * 1000),
            error=str(last_error)[:300] if last_error else "unknown",
        )
    )
    assert last_error is not None
    raise ActivityError(fn_name=name, attempts=attempts, cause=last_error)
