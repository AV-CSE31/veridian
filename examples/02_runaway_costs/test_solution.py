"""
Tests for Problem 2: Runaway Costs — CostGuardHook enforcement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from veridian.hooks.builtin.cost_guard import CostGuardHook
from veridian.core.exceptions import CostLimitExceeded


@dataclass
class _FakeResult:
    token_usage: dict[str, int] | None = None

@dataclass
class _FakeTask:
    id: str = "t1"
    result: Any = None

@dataclass
class _FakeEvent:
    task: Any = None


class TestBlocksRunawayCosts:
    """Prove $47K LangChain loop and $82K stolen key incidents are blocked."""

    def test_halts_when_budget_exhausted(self) -> None:
        hook = CostGuardHook(max_cost_usd=0.001, cost_per_token=0.000003)
        # Push past budget
        result = _FakeResult(token_usage={"total_tokens": 500})
        task = _FakeTask(id="t1", result=result)
        hook.after_task(_FakeEvent(task=task))

        # Next before_task should raise
        with pytest.raises(CostLimitExceeded):
            hook.before_task(_FakeEvent())

    def test_cumulative_tracking_across_tasks(self) -> None:
        hook = CostGuardHook(max_cost_usd=0.10, cost_per_token=0.000003)
        for i in range(5):
            result = _FakeResult(token_usage={"total_tokens": 5000})
            task = _FakeTask(id=f"t{i}", result=result)
            hook.after_task(_FakeEvent(task=task))
        # 5 * 5000 * 0.000003 = $0.075, still under $0.10
        hook.before_task(_FakeEvent())  # should not raise

    def test_exceeds_after_accumulation(self) -> None:
        hook = CostGuardHook(max_cost_usd=0.10, cost_per_token=0.000003)
        for i in range(10):
            result = _FakeResult(token_usage={"total_tokens": 5000})
            task = _FakeTask(id=f"t{i}", result=result)
            hook.after_task(_FakeEvent(task=task))
        # 10 * 5000 * 0.000003 = $0.15 > $0.10
        with pytest.raises(CostLimitExceeded):
            hook.before_task(_FakeEvent())


class TestPassesWithinBudget:
    """Prove legitimate work within budget completes."""

    def test_small_task_passes(self) -> None:
        hook = CostGuardHook(max_cost_usd=1.00, cost_per_token=0.000003)
        result = _FakeResult(token_usage={"total_tokens": 1000})
        task = _FakeTask(id="t1", result=result)
        hook.after_task(_FakeEvent(task=task))
        hook.before_task(_FakeEvent())  # should not raise

    def test_current_cost_property(self) -> None:
        hook = CostGuardHook(max_cost_usd=1.00, cost_per_token=0.000003)
        result = _FakeResult(token_usage={"total_tokens": 10000})
        task = _FakeTask(id="t1", result=result)
        hook.after_task(_FakeEvent(task=task))
        assert hook.current_cost == pytest.approx(0.03, abs=0.001)
