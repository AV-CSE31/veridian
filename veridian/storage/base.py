"""
veridian.storage.base
─────────────────────
BaseStorage ABC — uniform interface for all task storage backends.

All three backends (LocalJSONStorage, RedisStorage, PostgresStorage) must
implement this identical interface. No backend-specific callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from veridian.core.task import LedgerStats, Task, TaskResult

__all__ = ["BaseStorage"]


class BaseStorage(ABC):
    """
    Abstract base class for Veridian task storage backends.

    Implementations must be thread-safe and support concurrent callers.
    get_next() must acquire a distributed-safe lock to prevent double-claiming.
    """

    @abstractmethod
    def put(self, task: Task) -> None:
        """Insert or update a task in the backend."""

    @abstractmethod
    def get(self, task_id: str) -> Task:
        """
        Retrieve a task by ID.

        Raises:
            TaskNotFound: if no task with the given ID exists.
        """

    @abstractmethod
    def get_next(self) -> Task | None:
        """
        Return the highest-priority PENDING task whose dependencies are all DONE,
        atomically claiming it so no other caller receives the same task.

        Returns None if no eligible task exists.
        """

    @abstractmethod
    def complete(self, task_id: str, result: TaskResult) -> None:
        """
        Mark a task as DONE and persist its result.

        Raises:
            TaskNotFound: if no task with the given ID exists.
        """

    @abstractmethod
    def fail(self, task_id: str, error: str) -> None:
        """
        Mark a task as FAILED and store the error message.

        Raises:
            TaskNotFound: if no task with the given ID exists.
        """

    @abstractmethod
    def list_all(self) -> list[Task]:
        """Return all tasks in the backend, in any order."""

    @abstractmethod
    def stats(self) -> LedgerStats:
        """Return aggregate statistics over all tasks in the backend."""
