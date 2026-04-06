from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider
from veridian.testing.fault_injector import FaultInjector, FaultSchedule, FaultType


@pytest.fixture
def fault_schedule() -> FaultSchedule:
    return FaultSchedule(seed=42, fault_probability=0.5)


@pytest.fixture
def fault_injector(fault_schedule: FaultSchedule) -> FaultInjector:
    return FaultInjector(fault_schedule)


@pytest.fixture
def crash_schedule() -> FaultSchedule:
    return FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])


@pytest.fixture
def timeout_schedule() -> FaultSchedule:
    return FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.TIMEOUT])


@pytest.fixture
def stress_ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(
        path=tmp_path / "stress_ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def sample_task() -> Task:
    return Task(title="stress-test-task", description="A task for stress testing")


@pytest.fixture
def sample_result() -> TaskResult:
    return TaskResult(raw_output="stress test done", structured={"status": "ok"})
