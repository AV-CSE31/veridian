"""
Tests for Problem 2: Runaway Costs — CostGovernance pipeline.
Uses real CostGuardHook + CostGovernanceVerifier.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


_solution = _load_local_module("solution.py", f"{Path(__file__).parent.name}_solution")
CostGovernanceVerifier = _solution.CostGovernanceVerifier
CostTrackedTask = _solution.CostTrackedTask
run_cost_governed_pipeline = _solution.run_cost_governed_pipeline

from veridian.core.task import Task, TaskResult


class TestHaltsPipelineOnBudgetExhaustion:
    """Prove the $47K LangChain loop is stopped."""

    def test_halts_when_budget_exceeded(self) -> None:
        tasks = [
            CostTrackedTask("t1", "small", 10_000),
            CostTrackedTask("t2", "small", 10_000),
            CostTrackedTask("t3", "huge", 500_000),
            CostTrackedTask("t4", "never", 100_000),
        ]
        results = run_cost_governed_pipeline(tasks, budget_usd=0.05)
        executed = [t for t in results if t.executed]
        halted = [t for t in results if t.halted]
        assert len(executed) < len(tasks), "Budget should halt before all tasks run"
        assert len(halted) >= 1

    def test_early_tasks_execute(self) -> None:
        tasks = [
            CostTrackedTask("t1", "cheap", 1_000),
            CostTrackedTask("t2", "cheap", 1_000),
            CostTrackedTask("t3", "expensive", 1_000_000),
        ]
        results = run_cost_governed_pipeline(tasks, budget_usd=0.01)
        assert results[0].executed is True
        assert results[1].executed is True

    def test_halted_tasks_never_execute(self) -> None:
        tasks = [
            CostTrackedTask("t1", "fill", 50_000),
            CostTrackedTask("t2", "overflow", 50_000),
        ]
        results = run_cost_governed_pipeline(tasks, budget_usd=0.10)
        for t in results:
            if t.halted:
                assert t.actual_tokens == 0, "Halted tasks should not have consumed tokens"


class TestPerTaskTokenVerification:
    """Prove per-task token limits catch runaway individual tasks."""

    def test_blocks_single_task_exceeding_limit(self) -> None:
        verifier = CostGovernanceVerifier(max_tokens_per_task=10_000)
        task = Task(id="t1", title="check", verifier_id="cost_governance")
        result = TaskResult(raw_output="", token_usage={"total_tokens": 50_000})
        v = verifier.verify(task, result)
        assert v.passed is False
        assert "exceeds" in (v.error or "").lower()

    def test_passes_within_limit(self) -> None:
        verifier = CostGovernanceVerifier(max_tokens_per_task=50_000)
        task = Task(id="t1", title="check", verifier_id="cost_governance")
        result = TaskResult(raw_output="", token_usage={"total_tokens": 10_000})
        v = verifier.verify(task, result)
        assert v.passed is True

    def test_evidence_shows_utilization(self) -> None:
        verifier = CostGovernanceVerifier(max_tokens_per_task=100_000)
        task = Task(id="t1", title="check", verifier_id="cost_governance")
        result = TaskResult(raw_output="", token_usage={"total_tokens": 50_000})
        v = verifier.verify(task, result)
        assert v.evidence["utilization"] == pytest.approx(0.5)


class TestPipelineWithinBudget:
    """Prove legitimate workloads complete fully."""

    def test_all_tasks_complete_within_budget(self) -> None:
        tasks = [
            CostTrackedTask("t1", "small", 1_000),
            CostTrackedTask("t2", "small", 1_000),
            CostTrackedTask("t3", "small", 1_000),
        ]
        results = run_cost_governed_pipeline(tasks, budget_usd=1.00)
        assert all(t.executed for t in results)
        assert not any(t.halted for t in results)
