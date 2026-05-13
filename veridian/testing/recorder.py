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
        """Append one JSON line to the trace file under a FileLock.

        Previously this method read the entire trace file into memory,
        concatenated the new line, and rewrote the whole file through a
        temp file + ``os.replace``. That gave O(n) cost per append and an
        O(n²) cost across n records, which dominated long replay suites.

        The new path opens the trace file in binary-append mode under a
        process-cross FileLock so concurrent recorders serialise on the
        lock instead of racing on the temp-file rename. Crash safety is
        preserved by writing a complete newline-terminated JSON line
        before releasing the lock; a partial line on power loss is
        detected and skipped by the existing tolerant loader in
        :meth:`load`.
        """
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")

        lock_path = self._trace_file.with_suffix(self._trace_file.suffix + ".lock")
        filelock_cls: Any
        try:
            from filelock import FileLock as filelock_cls  # noqa: PLC0415
        except ImportError:
            filelock_cls = None

        if filelock_cls is None:
            # Best-effort: behave like the old code path when filelock is
            # not installed. Recorders are a dev/testing surface so this
            # fallback keeps the constructor usable in slim environments.
            with self._trace_file.open("ab") as fh:
                fh.write(line)
            return

        lock = filelock_cls(str(lock_path), timeout=15.0)
        with lock, self._trace_file.open("ab") as fh:
            fh.write(line)
