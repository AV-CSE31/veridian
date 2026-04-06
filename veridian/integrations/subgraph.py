"""
veridian.integrations.subgraph
───────────────────────────────
RV3-011: Subgraph / child-run composition model.

A subgraph is a nested RunContext that inherits the parent's runtime
(provider, ledger, verifier registry, activity journal) but keeps its own
trace step namespace and task scope. This lets an adapter spawn a child
verification boundary (e.g. a LangGraph subgraph or a CrewAI sub-crew)
without polluting the parent's audit trail.

Design rules:
- The child context has its own ``task_id`` — subgraph evidence is linked
  to the parent via ``metadata['parent_task_id']``.
- Child trace steps are namespaced with the subgraph id (``sg:{id}:...``)
  so parent/child events are distinguishable in replay.
- Failures in a child do NOT automatically fail the parent — the caller
  decides whether to propagate via the returned ``SubgraphResult``.
- The parent's activity journal is SHARED (same cache) so child LLM calls
  participate in the same replay semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veridian.core.task import Task, TaskResult, TraceStep
from veridian.integrations.sdk import RunContext, persist_state

__all__ = ["SubgraphResult", "start_subgraph", "complete_subgraph"]


@dataclass
class SubgraphResult:
    """Outcome of a child run.

    ``passed`` is True when every verified step in the subgraph succeeded.
    ``trace_steps`` is the list of steps recorded during the child run;
    these are namespaced so they cannot collide with parent step IDs.
    """

    subgraph_id: str
    parent_task_id: str
    child_task_id: str
    passed: bool
    trace_steps: list[TraceStep] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _namespace_trace_step(step: TraceStep, subgraph_id: str) -> TraceStep:
    """Prefix step_id with subgraph namespace and tag metadata."""
    namespaced_id = (
        step.step_id
        if step.step_id.startswith(f"sg:{subgraph_id}:")
        else f"sg:{subgraph_id}:{step.step_id}"
    )
    step.step_id = namespaced_id
    step.metadata = {**(step.metadata or {}), "subgraph_id": subgraph_id}
    return step


def start_subgraph(
    parent: RunContext,
    *,
    subgraph_id: str,
    child_task: Task,
) -> RunContext:
    """Create a child RunContext for a nested verification boundary.

    The child shares the parent's provider, ledger, verifier registry, and
    activity journal (so LLM replay is unified across the graph) but has its
    own ``trace_steps`` list and ``task_id``. The parent-child link lives in
    ``metadata['parent_task_id']``.

    The caller is responsible for adding ``child_task`` to the ledger before
    calling this function so ``persist_state`` has a target.
    """
    child = RunContext(
        run_id=f"{parent.run_id}:sg:{subgraph_id}",
        config=parent.config,
        ledger=parent.ledger,
        provider=parent.provider,
        verifier_registry=parent.verifier_registry,
        activity_journal=parent.activity_journal,  # shared for replay
        trace_steps=[],  # isolated
        task_id=child_task.id,
        metadata={
            "parent_task_id": parent.task_id,
            "parent_run_id": parent.run_id,
            "subgraph_id": subgraph_id,
        },
    )
    return child


def complete_subgraph(
    parent: RunContext,
    child: RunContext,
    *,
    passed: bool,
    error: str | None = None,
    persist: bool = True,
) -> SubgraphResult:
    """Finalize a child run and link its evidence back to the parent.

    - Namespaces every child trace step with ``sg:{subgraph_id}:``
    - Appends the child's namespaced steps to the parent's trace
    - Optionally persists both parent and child state to the ledger
    - Returns a ``SubgraphResult`` the caller uses to decide parent propagation
    """
    subgraph_id = str(child.metadata.get("subgraph_id", "unknown"))
    namespaced_steps = [_namespace_trace_step(s, subgraph_id) for s in child.trace_steps]

    # Link child evidence into parent trace (so replay sees the full picture).
    parent.trace_steps.extend(namespaced_steps)

    result = SubgraphResult(
        subgraph_id=subgraph_id,
        parent_task_id=str(child.metadata.get("parent_task_id") or ""),
        child_task_id=str(child.task_id or ""),
        passed=passed,
        trace_steps=list(namespaced_steps),
        error=error,
        metadata={
            "parent_run_id": child.metadata.get("parent_run_id"),
            "child_run_id": child.run_id,
        },
    )

    if persist:
        if child.task_id:
            # Annotate the child task's result with the parent link for audit.
            try:
                child_task = child.ledger.get(child.task_id)
                child_result = child_task.result or TaskResult(raw_output="")
                child_result.extras["parent_task_id"] = result.parent_task_id
                child_result.extras["subgraph_id"] = subgraph_id
                child.ledger.checkpoint_result(child.task_id, child_result)
            except Exception:
                # Best-effort audit annotation; never fail the subgraph because
                # of a persistence hiccup.
                pass
            persist_state(child, task_id=child.task_id)
        if parent.task_id:
            persist_state(parent, task_id=parent.task_id)

    return result
