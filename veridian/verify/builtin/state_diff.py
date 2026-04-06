"""
veridian.verify.builtin.state_diff
───────────────────────────────────
StateDiffVerifier — verifies ENVIRONMENT STATE, not just agent output.

GAP 5 FIX: Agents "hallucinate success" — they report task completion
based on the tool call's return message, not the actual environment state.

Research basis:
  Gemini Deep Research (2026): "An agent might report that a file was
  deleted because the tool call returned a success message, even if the
  file remains due to a system-level permission error."

  Agents of Chaos (Feb 2026): "Task success must be measured by
  computing s_target - s_actual, ensuring the environment state exactly
  matches the ground-truth target."

This verifier captures environment state BEFORE and AFTER task execution,
then computes a diff. The task passes only if the expected state change
actually occurred — not if the agent SAYS it occurred.

Example:
  Agent task: "Delete all .tmp files in /var/cache"
  Agent output: {"deleted": 47, "status": "complete"}
  Schema verifier: PASS (all fields present)
  StateDiff verifier: FAIL — 3 files remain due to permission errors

The difference between "the agent says it's done" and "the world shows
it's done."
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

__all__ = ["StateDiffVerifier", "StateSnapshot", "StateDiff"]

log = logging.getLogger(__name__)


@dataclass
class StateSnapshot:
    """Point-in-time capture of environment state.

    The snapshot is a dict of observable state properties. What to
    capture depends on the domain:
      - File operations: file existence, size, hash, permissions
      - Database operations: row counts, checksums
      - API operations: resource state, response codes
      - Infrastructure: container state, service health
    """

    timestamp: str = ""
    properties: dict[str, Any] = field(default_factory=dict)

    def hash(self) -> str:
        """SHA-256 of the snapshot for tamper detection."""
        import json

        canonical = json.dumps(self.properties, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class StateDiff:
    """Difference between pre-execution and post-execution state."""

    expected_changes: dict[str, Any] = field(default_factory=dict)
    actual_changes: dict[str, Any] = field(default_factory=dict)
    missing_changes: list[str] = field(default_factory=list)
    unexpected_changes: list[str] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        """True if actual changes match expected changes."""
        return len(self.missing_changes) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_changes": self.expected_changes,
            "actual_changes": self.actual_changes,
            "missing_changes": self.missing_changes,
            "unexpected_changes": self.unexpected_changes,
            "matches": self.matches,
        }


class StateDiffVerifier(BaseVerifier):
    """Verifies environment state changed as expected, not just agent output.

    Architecture:
      1. Before task: capture_fn() takes a pre-execution snapshot
      2. After task: capture_fn() takes a post-execution snapshot
      3. Verify: compute diff between snapshots vs expected changes
      4. PASS only if expected state changes actually occurred

    Usage:
      # File deletion task
      verifier = StateDiffVerifier(
          capture_fn=lambda: {"file_count": len(list(Path("/tmp").glob("*.tmp")))},
          expected_changes={"file_count": 0},  # all files should be gone
      )

      # Database row count task
      verifier = StateDiffVerifier(
          capture_fn=lambda: {"row_count": db.execute("SELECT COUNT(*) FROM users").scalar()},
          expected_changes={"row_count": 150},  # should have 150 rows after migration
      )
    """

    id: ClassVar[str] = "state_diff"
    description: ClassVar[str] = (
        "Verifies environment state changed as expected — catches "
        "hallucinated success where agent reports done but state unchanged"
    )

    def __init__(
        self,
        capture_fn: Callable[[], dict[str, Any]] | None = None,
        expected_changes: dict[str, Any] | None = None,
        tolerance: float = 0.0,
    ) -> None:
        self._capture_fn = capture_fn
        self._expected = expected_changes or {}
        self._tolerance = tolerance
        self._pre_snapshot: StateSnapshot | None = None

    def capture_pre_state(self) -> StateSnapshot:
        """Capture environment state BEFORE task execution.

        Called by VeridianRunner before dispatching to the worker agent.
        """
        if self._capture_fn is None:
            return StateSnapshot(properties={})
        props = self._capture_fn()
        snapshot = StateSnapshot(properties=props)
        self._pre_snapshot = snapshot
        log.info("state_diff.pre_capture properties=%d", len(props))
        return snapshot

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Compare post-execution state against expected changes.

        If no capture_fn was provided, falls back to checking the
        structured output for state-diff metadata (for cases where
        the agent reports what it changed and we verify the claim).
        """
        import time

        start = time.monotonic()

        # If capture_fn available, do real state diff
        if self._capture_fn is not None:
            post_state = self._capture_fn()
            diff = self._compute_diff(post_state)

            elapsed_ms = (time.monotonic() - start) * 1000

            if diff.matches:
                return VerificationResult(
                    passed=True,
                    evidence=diff.to_dict(),
                    verification_ms=elapsed_ms,
                )

            missing = ", ".join(diff.missing_changes[:3])
            return VerificationResult(
                passed=False,
                error=(
                    f"State verification failed: expected changes not observed. Missing: {missing}"
                ),
                evidence=diff.to_dict(),
                verification_ms=elapsed_ms,
            )

        # Fallback: verify structured output claims against task metadata
        structured = getattr(result, "structured", {}) or {}
        task_meta = getattr(task, "metadata", {}) or {}
        expected_state = task_meta.get("expected_state", {})

        if not expected_state:
            return VerificationResult(passed=True, evidence={"mode": "no_state_check"})

        mismatches: list[str] = []
        for key, expected_val in expected_state.items():
            actual_val = structured.get(key)
            if actual_val != expected_val:
                mismatches.append(f"{key}: expected={expected_val}, actual={actual_val}")

        elapsed_ms = (time.monotonic() - start) * 1000

        if mismatches:
            return VerificationResult(
                passed=False,
                error=f"State mismatch: {'; '.join(mismatches[:3])}",
                evidence={"mismatches": mismatches},
                verification_ms=elapsed_ms,
            )

        return VerificationResult(passed=True, verification_ms=elapsed_ms)

    def _compute_diff(self, post_state: dict[str, Any]) -> StateDiff:
        """Compute diff between pre-state, post-state, and expected changes."""
        pre = self._pre_snapshot.properties if self._pre_snapshot else {}
        diff = StateDiff(expected_changes=dict(self._expected))
        actual: dict[str, Any] = {}
        missing: list[str] = []
        unexpected: list[str] = []

        # Check each expected change
        for key, expected_val in self._expected.items():
            actual_val = post_state.get(key)
            actual[key] = actual_val

            if isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
                if abs(actual_val - expected_val) > self._tolerance:
                    missing.append(f"{key}: expected={expected_val}, actual={actual_val}")
            elif actual_val != expected_val:
                missing.append(f"{key}: expected={expected_val}, actual={actual_val}")

        # Detect unexpected changes (state changed in ways we didn't expect)
        for key in post_state:
            if key not in self._expected and key in pre and post_state[key] != pre[key]:
                unexpected.append(f"{key}: {pre[key]} -> {post_state[key]}")

        diff.actual_changes = actual
        diff.missing_changes = missing
        diff.unexpected_changes = unexpected
        return diff
