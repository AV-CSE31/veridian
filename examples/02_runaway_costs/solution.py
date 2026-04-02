"""
Problem 2: Runaway Cloud Costs — Agent Scales Without Constraints
=================================================================

INCIDENT: Mid-size SaaS company's AI agent scaled a cluster to 500 nodes
overnight, generating a $60,000 cloud bill by morning.

THIS SOLUTION: Uses Veridian's real CostGuardHook to enforce a hard USD
ceiling. The hook checks budget in before_task() and accumulates cost
in after_task(). When budget is exhausted, CostLimitExceeded fires.

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from veridian.hooks.builtin.cost_guard import CostGuardHook
from veridian.core.exceptions import CostLimitExceeded


@dataclass
class _FakeTask:
    """Minimal task stub matching what CostGuardHook reads."""
    id: str = ""
    result: Any = None


@dataclass
class _FakeResult:
    """Minimal result stub matching what CostGuardHook reads."""
    token_usage: dict[str, int] | None = None


@dataclass
class _FakeEvent:
    """Minimal event stub matching what CostGuardHook reads."""
    task: Any = None


def run_demo() -> None:
    """Simulate escalating costs using Veridian's REAL CostGuardHook."""
    # Real CostGuardHook with $0.10 ceiling for demo
    hook = CostGuardHook(max_cost_usd=0.10, cost_per_token=0.000_003, warn_at_pct=0.7)

    tasks = [
        ("optimize_query",        5_000),
        ("refactor_module",       8_000),
        ("generate_tests",       12_000),
        ("analyze_codebase",     15_000),
        ("rewrite_auth_module",  20_000),
        ("scale_infrastructure", 50_000),  # should trigger ceiling
        ("deploy_to_prod",      100_000),  # never reached
    ]

    print(f"\n{'=' * 65}")
    print(f"  Veridian CostGuardHook (REAL) -- Budget: $0.10")
    print(f"  Cost per token: $0.000003 (~$3/1M tokens)")
    print(f"{'=' * 65}")

    completed = 0
    for task_id, tokens in tasks:
        # before_task() checks if budget exhausted
        try:
            hook.before_task(_FakeEvent())
        except CostLimitExceeded as e:
            print(f"  [HALT] {task_id:25s}  BUDGET EXHAUSTED -- run stopped")
            print(f"         Cost: ${hook.current_cost:.6f} >= ${hook.max_cost_usd:.2f}")
            print(f"         Remaining {len(tasks) - completed} tasks cancelled")
            break

        # Simulate task execution and cost accumulation
        result = _FakeResult(token_usage={"total_tokens": tokens})
        task = _FakeTask(id=task_id, result=result)
        hook.after_task(_FakeEvent(task=task))

        cost_str = f"${tokens * 0.000003:.6f}"
        cumulative = f"${hook.current_cost:.6f}"
        print(f"  [PASS] {task_id:25s}  +{cost_str:>10s}  total={cumulative}")
        completed += 1

    print(f"\n  Completed: {completed}/{len(tasks)} tasks")
    print(f"  Final cost: ${hook.current_cost:.6f}")
    print(f"  Budget: ${hook.max_cost_usd:.2f}")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    run_demo()
