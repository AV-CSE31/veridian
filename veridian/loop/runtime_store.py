"""
veridian.loop.runtime_store
──────────────────────────
Runtime storage abstraction for runner-facing state operations.

This protocol is intentionally shaped around the operations Veridian runners
need (claim, pause/resume, checkpoint, terminal transitions), rather than a
specific backend class. TaskLedger conforms to this protocol, and future
runtime backends can implement the same contract.
"""

from __future__ import annotations

import builtins
from typing import Any, Protocol, runtime_checkable

from veridian.core.task import LedgerStats, Task, TaskResult, TaskStatus

__all__ = ["RuntimeStore"]


@runtime_checkable
class RuntimeStore(Protocol):
    """Runner-facing storage contract.

    Backends implementing this protocol can power VeridianRunner,
    ParallelRunner, and SDK persistence without exposing backend-specific
    internals to orchestration logic.
    """

    def get(self, task_id: str) -> Task: ...

    def get_next(
        self,
        phase: str | None = None,
        respect_dependencies: bool = True,
        include_paused: bool = False,
    ) -> Task | None: ...

    def list(
        self,
        status: TaskStatus | str | None = None,
        phase: str | None = None,
        priority_gte: int | None = None,
    ) -> builtins.list[Task]: ...

    def stats(self) -> LedgerStats: ...

    def phases(self) -> builtins.list[str]: ...

    def add(self, tasks: builtins.list[Task], skip_duplicates: bool = True) -> int: ...

    def claim(self, task_id: str, runner_id: str) -> Task: ...

    def submit_result(self, task_id: str, result: TaskResult) -> Task: ...

    def checkpoint_result(self, task_id: str, result: TaskResult) -> Task: ...

    def mark_done(self, task_id: str, result: TaskResult) -> Task: ...

    def mark_failed(self, task_id: str, error: str) -> Task: ...

    def skip(self, task_id: str, reason: str = "") -> Task: ...

    def pause(
        self,
        task_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> Task: ...

    def resume(self, task_id: str, runner_id: str) -> Task: ...

    def reset_in_progress(self, runner_id: str | None = None) -> int: ...
