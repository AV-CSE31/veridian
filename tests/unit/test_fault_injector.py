from __future__ import annotations

import pytest

from veridian.core.exceptions import ExecutorTimeout, ProviderError, VeridianError
from veridian.testing.fault_injector import (
    FaultInjector,
    FaultSchedule,
    FaultType,
    InjectedCrash,
    InjectedPartialWrite,
)


def test_fault_type_variants_are_stable() -> None:
    assert FaultType.CRASH == "crash"
    assert FaultType.TIMEOUT == "timeout"
    assert FaultType.PROVIDER_ERROR == "provider_error"
    assert FaultType.PARTIAL_WRITE == "partial_write"


def test_schedule_defaults_include_all_fault_types() -> None:
    schedule = FaultSchedule(seed=42)
    assert schedule.fault_probability == 0.5
    assert schedule.fault_types == list(FaultType)


def test_same_seed_produces_same_fault_pattern() -> None:
    schedule = FaultSchedule(seed=42, fault_probability=0.5)
    a = FaultInjector(schedule)
    b = FaultInjector(schedule)
    assert [a.should_fault(i) for i in range(25)] == [b.should_fault(i) for i in range(25)]


def test_different_seed_produces_different_fault_pattern() -> None:
    a = FaultInjector(FaultSchedule(seed=42, fault_probability=0.5))
    b = FaultInjector(FaultSchedule(seed=99, fault_probability=0.5))
    assert [a.should_fault(i) for i in range(40)] != [b.should_fault(i) for i in range(40)]


@pytest.mark.parametrize(
    ("fault_type", "expected"),
    [
        (FaultType.CRASH, InjectedCrash),
        (FaultType.TIMEOUT, ExecutorTimeout),
        (FaultType.PROVIDER_ERROR, ProviderError),
        (FaultType.PARTIAL_WRITE, InjectedPartialWrite),
    ],
)
def test_inject_raises_expected_exception(
    fault_type: FaultType,
    expected: type[BaseException],
) -> None:
    injector = FaultInjector(FaultSchedule(seed=7, fault_probability=1.0, fault_types=[fault_type]))
    with pytest.raises(expected):
        injector.inject(0)


def test_all_injected_faults_inherit_veridian_error() -> None:
    for fault_type in FaultType:
        injector = FaultInjector(
            FaultSchedule(seed=7, fault_probability=1.0, fault_types=[fault_type])
        )
        with pytest.raises(VeridianError):
            injector.inject(0)


def test_probability_zero_never_faults() -> None:
    injector = FaultInjector(FaultSchedule(seed=42, fault_probability=0.0))
    assert all(injector.should_fault(i) is None for i in range(50))


def test_inject_at_step_only_fires_on_target() -> None:
    injector = FaultInjector(
        FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])
    )
    for step in range(3):
        injector.inject_at_step(step, target_step=99)
    with pytest.raises(InjectedCrash):
        injector.inject_at_step(3, target_step=3)


def test_crash_context_raises_when_fault_is_scheduled() -> None:
    injector = FaultInjector(
        FaultSchedule(seed=42, fault_probability=1.0, fault_types=[FaultType.CRASH])
    )
    with pytest.raises(InjectedCrash), injector.crash_context(0):
        pass


def test_crash_context_passes_when_no_fault() -> None:
    injector = FaultInjector(FaultSchedule(seed=42, fault_probability=0.0))
    executed = False
    with injector.crash_context(0):
        executed = True
    assert executed
