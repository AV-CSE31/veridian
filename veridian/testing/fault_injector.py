"""
veridian.testing.fault_injector
-------------------------------
Deterministic failure-injection harness for chaos and stress testing.
"""

from __future__ import annotations

import contextlib
import logging
import random
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import StrEnum

from veridian.core.exceptions import ExecutorTimeout, ProviderError, VeridianError

__all__ = [
    "FaultInjector",
    "FaultSchedule",
    "FaultType",
    "InjectedCrash",
    "InjectedPartialWrite",
]

log = logging.getLogger(__name__)


class InjectedCrash(VeridianError):
    """Simulated process crash injected by FaultInjector."""

    def __init__(self, step: int) -> None:
        self.step = step
        super().__init__(f"Injected crash at step {step}")


class InjectedPartialWrite(VeridianError):
    """Simulated partial write injected by FaultInjector."""

    def __init__(self, step: int) -> None:
        self.step = step
        super().__init__(f"Injected partial write failure at step {step}")


class FaultType(StrEnum):
    """Types of faults that can be injected into a test run."""

    CRASH = "crash"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    PARTIAL_WRITE = "partial_write"


@dataclass(frozen=True)
class FaultSchedule:
    """Configuration for deterministic fault decisions."""

    seed: int
    fault_probability: float = 0.5
    fault_types: list[FaultType] = field(default_factory=lambda: list(FaultType))


class FaultInjector:
    """Deterministic step-based fault injector."""

    def __init__(self, schedule: FaultSchedule) -> None:
        self._schedule = schedule
        self._cache: dict[int, FaultType | None] = {}

    def should_fault(self, step: int) -> FaultType | None:
        """Return the fault for step, or None."""
        if step in self._cache:
            return self._cache[step]

        rng = random.Random(self._schedule.seed ^ step)  # noqa: S311
        if rng.random() >= self._schedule.fault_probability or not self._schedule.fault_types:
            self._cache[step] = None
            return None

        fault = rng.choice(self._schedule.fault_types)
        self._cache[step] = fault
        return fault

    def inject(self, step: int) -> None:
        """Inject scheduled fault at step, if any."""
        fault = self.should_fault(step)
        if fault is None:
            return
        self._raise_for(fault, step)

    def inject_at_step(self, current_step: int, *, target_step: int) -> None:
        """Inject fault only when current_step equals target_step."""
        if current_step != target_step:
            return
        self.inject(current_step)

    @contextlib.contextmanager
    def crash_context(self, step: int) -> Generator[None, None, None]:
        """Context manager variant of inject()."""
        self.inject(step)
        yield

    @staticmethod
    def _raise_for(fault: FaultType, step: int) -> None:
        if fault == FaultType.CRASH:
            raise InjectedCrash(step)
        if fault == FaultType.TIMEOUT:
            raise ExecutorTimeout(f"Injected timeout at step {step}")
        if fault == FaultType.PROVIDER_ERROR:
            raise ProviderError(f"Injected provider error at step {step}")
        if fault == FaultType.PARTIAL_WRITE:
            raise InjectedPartialWrite(step)
        raise VeridianError(f"Unknown fault type {fault!r} at step {step}")  # pragma: no cover
