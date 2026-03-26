"""
veridian.core
──────────────
Core domain models, events, exceptions, and configuration.
"""

from veridian.core.config import VeridianConfig
from veridian.core.exceptions import VeridianError
from veridian.core.task import LedgerStats, Task, TaskPriority, TaskResult, TaskStatus

__all__ = [
    "Task",
    "TaskStatus",
    "TaskResult",
    "TaskPriority",
    "LedgerStats",
    "VeridianError",
    "VeridianConfig",
]
