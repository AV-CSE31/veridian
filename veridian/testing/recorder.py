"""
veridian.testing.recorder
──────────────────────────
AgentRecorder — captures agent executions as deterministic replay traces.

Each recorded run is serialized as a JSONL line in a trace file.  The
``AgentRecorder`` is designed to be lightweight: it captures task input,
agent output, and verification outcome without re-running verification logic.

Usage::

    recorder = AgentRecorder(trace_dir=Path("traces"))
    recorder.record(
        run_id="run-001",
        task=task,
        result=task_result,
        verification_passed=True,
    )
    runs = recorder.load()
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veridian.core.task import Task, TaskResult

__all__ = ["AgentRecorder", "RecordedRun"]

_DEFAULT_TRACE_FILENAME = "replay_trace.jsonl"


# ── RecordedRun ───────────────────────────────────────────────────────────────


@dataclass
class RecordedRun:
    """A single captured agent execution suitable for replay and assertion."""

    run_id: str
    task: Task
    result: TaskResult
    verification_passed: bool
    verification_error: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "run_id": self.run_id,
            "task": self.task.to_dict(),
            "result": self.result.to_dict(),
            "verification_passed": self.verification_passed,
            "verification_error": self.verification_error,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RecordedRun:
        """Deserialize from a JSON-safe dict."""
        return cls(
            run_id=d["run_id"],
            task=Task.from_dict(d["task"]),
            result=TaskResult.from_dict(d["result"]),
            verification_passed=d["verification_passed"],
            verification_error=d.get("verification_error"),
            recorded_at=d.get("recorded_at", ""),
        )


# ── AgentRecorder ─────────────────────────────────────────────────────────────


class AgentRecorder:
    """
    Records agent executions to a JSONL replay trace file.

    Usage::

        recorder = AgentRecorder(trace_dir=Path("traces"))
        recorder.record(run_id="r1", task=task, result=result, verification_passed=True)
        runs = recorder.load()
    """

    def __init__(
        self,
        trace_dir: Path | None = None,
        filename: str = _DEFAULT_TRACE_FILENAME,
    ) -> None:
        """Initialize recorder with output directory."""
        self.trace_dir = trace_dir or Path("veridian_traces")
        self.filename = filename
        self._trace_file = self.trace_dir / self.filename

    @property
    def trace_file(self) -> Path:
        """Path to the JSONL replay trace file."""
        return self._trace_file

    def record(
        self,
        run_id: str,
        task: Task,
        result: TaskResult,
        verification_passed: bool,
        verification_error: str | None = None,
    ) -> RecordedRun:
        """Append one recorded run to the trace file.  Returns the RecordedRun."""
        rec = RecordedRun(
            run_id=run_id,
            task=task,
            result=result,
            verification_passed=verification_passed,
            verification_error=verification_error,
        )
        self._atomic_append(rec.to_dict())
        return rec

    def load(self) -> list[RecordedRun]:
        """Load all recorded runs from the trace file."""
        if not self._trace_file.exists():
            return []
        runs: list[RecordedRun] = []
        for line in self._trace_file.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(RecordedRun.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue
        return runs

    # ── Internal ──────────────────────────────────────────────────────────────

    def _atomic_append(self, data: dict[str, Any]) -> None:
        """Atomically append one JSON line to the trace file."""
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(data, ensure_ascii=False) + "\n"

        # Read existing + append (atomic via temp file + os.replace)
        existing = b""
        if self._trace_file.exists():
            existing = self._trace_file.read_bytes()

        new_content = existing + line.encode("utf-8")

        fd, tmp = tempfile.mkstemp(dir=self.trace_dir, prefix=".trace_", suffix=".tmp")
        try:
            os.write(fd, new_content)
            os.close(fd)
            os.replace(tmp, self._trace_file)
        except Exception:
            import contextlib

            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
