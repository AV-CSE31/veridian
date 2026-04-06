"""
tests.integration.test_subgraph
────────────────────────────────
RV3-011: Subgraph composition model with isolated state + parent evidence
linking. Uses SDK primitives directly (no adapter) so failures pinpoint the
composition layer rather than framework glue.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TraceStep
from veridian.integrations.sdk import record_step, start_run
from veridian.integrations.subgraph import (
    complete_subgraph,
    start_subgraph,
)
from veridian.ledger.ledger import TaskLedger
from veridian.providers.mock_provider import MockProvider


@pytest.fixture
def env(tmp_path: Path) -> tuple[VeridianConfig, MockProvider, TaskLedger]:
    cfg = VeridianConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        activity_journal_enabled=True,
    )
    provider = MockProvider()
    provider.model = "mock/v1"  # type: ignore[attr-defined]
    ledger = TaskLedger(path=cfg.ledger_file, progress_file=str(cfg.progress_file))
    return cfg, provider, ledger


def _task(ledger: TaskLedger, title: str) -> Task:
    t = Task(title=title, verifier_id="schema", verifier_config={"required_fields": ["summary"]})
    ledger.add([t])
    return t


def _step(name: str) -> TraceStep:
    return TraceStep(
        step_id=name,
        role="assistant",
        action_type="reason",
        content=f"content for {name}",
        timestamp_ms=int(time.time() * 1000),
    )


class TestSubgraphIsolation:
    def test_child_has_isolated_trace_steps(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        cfg, provider, ledger = env
        parent_task = _task(ledger, "parent")
        child_task = _task(ledger, "child")
        parent_ctx = start_run(config=cfg, provider=provider, ledger=ledger)
        parent_ctx.task_id = parent_task.id
        record_step(parent_ctx, _step("p1"))

        child_ctx = start_subgraph(parent_ctx, subgraph_id="sg1", child_task=child_task)
        record_step(child_ctx, _step("c1"))
        record_step(child_ctx, _step("c2"))

        # Before complete_subgraph, parent has only its own steps.
        assert [s.step_id for s in parent_ctx.trace_steps] == ["p1"]
        assert len(child_ctx.trace_steps) == 2

    def test_complete_subgraph_namespaces_and_links_evidence(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        cfg, provider, ledger = env
        parent_task = _task(ledger, "parent")
        child_task = _task(ledger, "child")
        parent_ctx = start_run(config=cfg, provider=provider, ledger=ledger)
        parent_ctx.task_id = parent_task.id
        record_step(parent_ctx, _step("p1"))

        child_ctx = start_subgraph(parent_ctx, subgraph_id="sg1", child_task=child_task)
        record_step(child_ctx, _step("step_a"))
        record_step(child_ctx, _step("step_b"))

        result = complete_subgraph(parent_ctx, child_ctx, passed=True)

        # Namespacing applied
        assert all(s.step_id.startswith("sg:sg1:") for s in result.trace_steps)
        # Parent now sees its own step plus the namespaced child steps
        parent_step_ids = [s.step_id for s in parent_ctx.trace_steps]
        assert "p1" in parent_step_ids
        assert any(sid.startswith("sg:sg1:") for sid in parent_step_ids)
        # Result carries parent link
        assert result.parent_task_id == parent_task.id
        assert result.child_task_id == child_task.id
        assert result.passed is True

    def test_child_extras_link_back_to_parent_task(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        cfg, provider, ledger = env
        parent_task = _task(ledger, "parent")
        child_task = _task(ledger, "child")
        parent_ctx = start_run(config=cfg, provider=provider, ledger=ledger)
        parent_ctx.task_id = parent_task.id
        child_ctx = start_subgraph(parent_ctx, subgraph_id="sg2", child_task=child_task)
        record_step(child_ctx, _step("x"))
        complete_subgraph(parent_ctx, child_ctx, passed=True)

        stored_child = ledger.get(child_task.id)
        assert stored_child.result is not None
        assert stored_child.result.extras.get("parent_task_id") == parent_task.id
        assert stored_child.result.extras.get("subgraph_id") == "sg2"


class TestSubgraphFailureDoesNotAutoFailParent:
    def test_failed_subgraph_returns_passed_false_without_raising(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        cfg, provider, ledger = env
        parent_task = _task(ledger, "parent")
        child_task = _task(ledger, "child")
        parent_ctx = start_run(config=cfg, provider=provider, ledger=ledger)
        parent_ctx.task_id = parent_task.id
        child_ctx = start_subgraph(parent_ctx, subgraph_id="sg3", child_task=child_task)
        record_step(child_ctx, _step("bad_step"))
        result = complete_subgraph(
            parent_ctx,
            child_ctx,
            passed=False,
            error="verification rejected child output",
        )
        assert result.passed is False
        assert result.error == "verification rejected child output"
        # Parent is still alive — caller decides whether to propagate.
        assert parent_ctx.task_id == parent_task.id


class TestSharedActivityJournal:
    def test_child_shares_parent_activity_journal(
        self, env: tuple[VeridianConfig, MockProvider, TaskLedger]
    ) -> None:
        """LLM cache must be unified across the graph so a child doesn't
        re-call the provider for something the parent already cached."""
        cfg, provider, ledger = env
        parent_task = _task(ledger, "parent")
        child_task = _task(ledger, "child")
        parent_ctx = start_run(config=cfg, provider=provider, ledger=ledger)
        parent_ctx.task_id = parent_task.id
        child_ctx = start_subgraph(parent_ctx, subgraph_id="sg4", child_task=child_task)
        assert child_ctx.activity_journal is parent_ctx.activity_journal
