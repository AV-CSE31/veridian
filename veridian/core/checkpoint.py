"""
veridian.core.checkpoint
─────────────────────────
Checkpoint / Restore for long-running agent tasks.

Periodic state snapshots let a crashed or interrupted run resume from
the last good checkpoint rather than starting over.

Storage is agnostic — checkpoint files are written to a configurable
directory using atomic temp-file + os.replace() (CLAUDE.md §1.3).

Usage::

    config = CheckpointConfig(interval_steps=10, max_checkpoints=5)
    mgr = CheckpointManager(storage_dir=Path("/tmp/checkpoints"), run_id="run_001")

    # During a long loop:
    for step_num, item in enumerate(work_items, start=1):
        process(item)
        cp = mgr.maybe_save(step=step_num, task_state={"step": step_num, ...})

    # On restart after crash:
    mgr = CheckpointManager(storage_dir=Path("/tmp/checkpoints"), run_id="run_001")
    last = mgr.restore_latest()
    if last:
        resume_from = last.task_state["step"]
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veridian.core.exceptions import CheckpointError

log = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """
    A serialisable snapshot of task state at a point in time.

    Attributes:
        checkpoint_id:        Unique ID (auto-generated if not provided).
        task_state:           Arbitrary dict with the task's progress state.
        verification_history: List of past verification results.
        agent_context:        Provider/model context (tokens used, model name, etc.).
        created_at:           Wall-clock time of the snapshot.
    """

    checkpoint_id: str
    task_state: dict[str, Any]
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    agent_context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_state": self.task_state,
            "verification_history": self.verification_history,
            "agent_context": self.agent_context,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(
            checkpoint_id=d["checkpoint_id"],
            task_state=d.get("task_state", {}),
            verification_history=d.get("verification_history", []),
            agent_context=d.get("agent_context", {}),
            created_at=d.get("created_at", datetime.now(tz=UTC).isoformat()),
        )


@dataclass
class CheckpointConfig:
    """
    Configuration for the CheckpointManager.

    Args:
        interval_steps:  Save a checkpoint every N steps (default 10).
        max_checkpoints: Keep at most N checkpoints; evict oldest (default 5).
    """

    interval_steps: int = 10
    max_checkpoints: int = 5


class CheckpointManager:
    """
    Manages saving and restoring checkpoints for a single run.

    All checkpoint files are stored in ``{storage_dir}/{run_id}/``.
    Filenames are ``{timestamp}_{checkpoint_id}.json`` so they sort
    chronologically.

    Thread-safety: Not thread-safe. One manager per run.
    """

    def __init__(
        self,
        storage_dir: Path,
        run_id: str,
        config: CheckpointConfig | None = None,
    ) -> None:
        self._run_dir = Path(storage_dir) / run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.config = config or CheckpointConfig()

    # ── Public API ─────────────────────────────────────────────────────────────

    def save(
        self,
        task_state: dict[str, Any],
        verification_history: list[dict[str, Any]] | None = None,
        agent_context: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """
        Save a checkpoint unconditionally.

        Returns the saved Checkpoint.
        After saving, evicts oldest checkpoints beyond max_checkpoints.
        """
        cp = Checkpoint(
            checkpoint_id=str(uuid.uuid4())[:12],
            task_state=task_state,
            verification_history=verification_history or [],
            agent_context=agent_context or {},
        )
        self._write(cp)
        self._evict_old()
        log.debug(
            "checkpoint.save run_id=%s cp_id=%s",
            self.run_id,
            cp.checkpoint_id,
        )
        return cp

    def maybe_save(
        self,
        step: int,
        task_state: dict[str, Any],
        verification_history: list[dict[str, Any]] | None = None,
        agent_context: dict[str, Any] | None = None,
    ) -> Checkpoint | None:
        """
        Save a checkpoint if ``step`` is a multiple of ``interval_steps``.

        Returns the Checkpoint if saved, or None if skipped.
        """
        if step % self.config.interval_steps == 0:
            return self.save(
                task_state=task_state,
                verification_history=verification_history,
                agent_context=agent_context,
            )
        return None

    def restore_latest(self) -> Checkpoint | None:
        """
        Return the most recent checkpoint, or None if no checkpoints exist.
        """
        checkpoints = self.list_checkpoints()
        return checkpoints[0] if checkpoints else None

    def list_checkpoints(self) -> list[Checkpoint]:
        """
        Return all checkpoints sorted newest-first.
        """
        cp_files = sorted(self._run_dir.glob("*.json"), reverse=True)
        result: list[Checkpoint] = []
        for path in cp_files:
            try:
                cp = self._read(path)
                result.append(cp)
            except Exception as exc:
                log.warning("checkpoint.read_error path=%s err=%s", path, exc)
        return result

    def clear(self) -> None:
        """Delete all checkpoint files for this run."""
        for path in self._run_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError as exc:
                log.warning("checkpoint.clear_error path=%s err=%s", path, exc)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _checkpoint_path(self, cp: Checkpoint) -> Path:
        # Prefix with ISO timestamp so files sort chronologically.
        ts = cp.created_at.replace(":", "-").replace(".", "-")
        return self._run_dir / f"{ts}_{cp.checkpoint_id}.json"

    def _write(self, cp: Checkpoint) -> None:
        """Atomic write: temp file + os.replace()."""
        dest = self._checkpoint_path(cp)
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self._run_dir,
                delete=False,
                suffix=".tmp",
                encoding="utf-8",
            ) as f:
                json.dump(cp.to_dict(), f, indent=2)
                tmp = Path(f.name)
            os.replace(tmp, dest)
        except OSError as exc:
            raise CheckpointError(
                f"Failed to write checkpoint {cp.checkpoint_id}: {exc}"
            ) from exc

    def _read(self, path: Path) -> Checkpoint:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return Checkpoint.from_dict(data)
        except Exception as exc:
            raise CheckpointError(f"Failed to read checkpoint {path}: {exc}") from exc

    def _evict_old(self) -> None:
        """Remove checkpoint files beyond max_checkpoints, oldest first."""
        cp_files = sorted(self._run_dir.glob("*.json"))
        excess = len(cp_files) - self.config.max_checkpoints
        if excess > 0:
            for path in cp_files[:excess]:
                try:
                    path.unlink()
                    log.debug("checkpoint.evict path=%s", path)
                except OSError as exc:
                    log.warning("checkpoint.evict_error path=%s err=%s", path, exc)


__all__ = ["Checkpoint", "CheckpointManager", "CheckpointConfig"]
