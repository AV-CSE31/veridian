"""
veridian.agents.base
─────────────────────
BaseAgent ABC. All agents inherit from this.
Agents are stateless coordinators — they hold no mutable per-task state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

__all__ = ["BaseAgent"]


class BaseAgent(ABC):
    """
    Abstract base for all Veridian agents.

    Subclasses must set a unique class-level id and implement run().
    All dependencies are injected via __init__ (no hard instantiation).
    """

    id: ClassVar[str] = ""

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute agent logic. Return a TaskResult or Task."""
