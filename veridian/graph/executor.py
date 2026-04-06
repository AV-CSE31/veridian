"""
veridian.graph.executor
────────────────────────
Graph execution engine with superstep barriers, checkpoint/resume,
and hook event firing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from veridian.core.exceptions import GraphError
from veridian.core.task import Task, TaskResult
from veridian.graph.state import GraphEdge, GraphState, NodeStatus, NodeType
from veridian.graph.superstep import Superstep, SuperstepScheduler
from veridian.graph.verified_edge import EdgeVerifier, VerifiedEdge

__all__ = [
    "GraphExecutor",
]

log = logging.getLogger(__name__)


class GraphExecutor:
    """
    Executes a graph superstep-by-superstep with barrier synchronization.

    Parameters
    ----------
    graph_state :
        The mutable graph to execute.
    scheduler :
        Computes superstep partitions.
    node_callback :
        Called for each TASK node: ``callback(node_id, graph_state) -> TaskResult``.
    context :
        Shared context dict passed to decision-node condition lambdas.
    edge_verifier :
        Optional edge verifier for VerifiedEdge checks.
    task_factory :
        Factory to create a Task for edge verification (needed by verifiers).
    result_factory :
        Factory to create a TaskResult for edge verification.
    checkpoint_dir :
        Directory for atomic checkpoint files. None disables checkpointing.
    hooks :
        Optional dict of hook name -> callable for lifecycle events.
    """

    def __init__(
        self,
        graph_state: GraphState,
        scheduler: SuperstepScheduler,
        node_callback: Callable[[str, GraphState], TaskResult],
        context: dict[str, Any] | None = None,
        edge_verifier: EdgeVerifier | None = None,
        task_factory: Callable[[], Task] | None = None,
        result_factory: Callable[[], TaskResult] | None = None,
        checkpoint_dir: Path | None = None,
        hooks: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._gs = graph_state
        self._scheduler = scheduler
        self._callback = node_callback
        self._context = context or {}
        self._edge_verifier = edge_verifier
        self._task_factory = task_factory
        self._result_factory = result_factory
        self._checkpoint_dir = checkpoint_dir
        self._hooks = hooks or {}
        self._supersteps: list[Superstep] | None = None
        self._current_step_index: int = 0

    # ── Public API ───────────────────────────────────────────────────────────

    def execute(self) -> None:
        """Run the full graph to completion, superstep by superstep."""
        self._fire_hook("graph_started", self._gs)

        # Check for loops
        has_loops = any(n.max_iterations > 0 for n in self._gs.nodes.values())

        if has_loops:
            self._execute_with_loops()
        else:
            self._execute_dag()

        self._fire_hook("graph_completed", self._gs)

    def execute_one_superstep(self) -> bool:
        """
        Execute the next pending superstep.

        Returns True if a superstep was executed, False if all are complete.
        """
        if self._supersteps is None:
            self._supersteps = self._scheduler.compute_supersteps(self._gs)

        if self._current_step_index >= len(self._supersteps):
            return False

        step = self._supersteps[self._current_step_index]
        self._execute_superstep(step)
        self._current_step_index += 1
        return True

    def save_checkpoint(self) -> Path:
        """
        Save current graph state to a checkpoint file using atomic write.

        Returns the path to the checkpoint file.
        Raises GraphError if checkpoint_dir is not configured.
        """
        if self._checkpoint_dir is None:
            raise GraphError("Checkpoint directory not configured")

        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        state_dict = self._gs.to_dict()
        state_dict["_executor_step_index"] = self._current_step_index

        cp_path = self._checkpoint_dir / f"graph_checkpoint_{self._current_step_index}.json"

        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=self._checkpoint_dir,
                delete=False,
                suffix=".tmp",
                encoding="utf-8",
            ) as f:
                json.dump(state_dict, f, indent=2)
                tmp_path = Path(f.name)
            os.replace(tmp_path, cp_path)
        except OSError as exc:
            raise GraphError(f"Failed to save checkpoint: {exc}") from exc

        log.info("graph.checkpoint_saved path=%s step=%d", cp_path, self._current_step_index)
        return cp_path

    def restore_checkpoint(self, cp_path: Path) -> None:
        """
        Restore graph state from a checkpoint file.

        Updates the internal graph state and step index.
        """
        try:
            with cp_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphError(f"Failed to restore checkpoint: {exc}") from exc

        step_index = data.pop("_executor_step_index", 0)

        # Restore node statuses and loop counters from checkpoint
        restored = GraphState.from_dict(data)
        for nid, node in restored.nodes.items():
            if nid in self._gs.nodes:
                self._gs.nodes[nid].status = node.status
        self._gs._loop_counters = dict(restored._loop_counters)

        self._current_step_index = step_index
        self._supersteps = None  # Force recompute

        log.info("graph.checkpoint_restored path=%s step=%d", cp_path, step_index)

    # ── Internal execution ───────────────────────────────────────────────────

    def _execute_dag(self) -> None:
        """Execute a DAG (no loops) using superstep barriers."""
        supersteps = self._scheduler.compute_supersteps(self._gs)
        self._supersteps = supersteps

        # Skip already-completed supersteps (resume support)
        start = self._current_step_index
        for i in range(start, len(supersteps)):
            step = supersteps[i]
            if self._scheduler.is_superstep_complete(self._gs, step):
                self._current_step_index = i + 1
                continue
            self._execute_superstep(step)
            self._current_step_index = i + 1

            if self._checkpoint_dir is not None:
                self.save_checkpoint()

    def _execute_with_loops(self) -> None:
        """Execute a graph with loop-bounded back-edges."""
        max_global_iterations = (
            sum(max(n.max_iterations, 1) for n in self._gs.nodes.values()) * 2
        )  # Safety bound

        iteration = 0
        while iteration < max_global_iterations:
            iteration += 1

            # Find all nodes that are ready to execute
            ready = self._get_ready_nodes_for_execution()
            if not ready:
                break

            for node_id in ready:
                self._execute_node(node_id)

            # After all ready nodes executed, check for loop re-activation
            self._handle_loop_reactivation()

    def _execute_superstep(self, step: Superstep) -> None:
        """Execute all nodes in a single superstep."""
        self._fire_hook("superstep_started", step)

        for node_id in step.node_ids:
            node = self._gs.nodes[node_id]
            if node.status == NodeStatus.COMPLETED:
                continue
            if node.status == NodeStatus.SKIPPED:
                continue
            self._execute_node(node_id)

        # After executing, skip unreachable nodes (decision branches not taken)
        self._skip_unreachable_in_superstep(step)

        self._fire_hook("superstep_completed", step)

    def _execute_node(self, node_id: str) -> None:
        """Execute a single node based on its type."""
        node = self._gs.nodes[node_id]

        if node.node_type == NodeType.TASK:
            self._execute_task_node(node_id)
        elif node.node_type == NodeType.DECISION:
            self._execute_decision_node(node_id)
        elif node.node_type == NodeType.FORK:
            self._execute_fork_node(node_id)
        elif node.node_type == NodeType.JOIN:
            self._execute_join_node(node_id)

    def _execute_task_node(self, node_id: str) -> None:
        """Execute a task node via the callback."""
        # Enforce verified incoming edges for generic TASK nodes.
        incoming = [
            edge
            for edge in self._gs.edges
            if edge.target == node_id and self._gs.nodes[edge.source].status == NodeStatus.COMPLETED
        ]
        if incoming and any(not self._edge_allows(edge) for edge in incoming):
            self._gs.advance_node(node_id, NodeStatus.SKIPPED)
            self._fire_hook("node_skipped", node_id)
            return

        self._gs.advance_node(node_id, NodeStatus.RUNNING)
        self._fire_hook("node_started", node_id)

        try:
            _ = self._callback(node_id, self._gs)
            self._gs.advance_node(node_id, NodeStatus.COMPLETED)
            self._fire_hook("node_completed", node_id)
        except Exception as exc:
            self._gs.advance_node(node_id, NodeStatus.FAILED)
            self._fire_hook("node_failed", node_id)
            log.error("graph.node_failed node_id=%s error=%s", node_id, exc)

    def _execute_decision_node(self, node_id: str) -> None:
        """Evaluate decision conditions and activate matching branches."""
        self._gs.advance_node(node_id, NodeStatus.RUNNING)

        activated = self._gs.get_activated_edges(node_id, self._context)
        activated_targets = {e.target for e in activated}

        # Skip non-activated targets
        all_outgoing = [e for e in self._gs.edges if e.source == node_id]
        for edge in all_outgoing:
            if edge.target not in activated_targets:
                self._skip_subtree(edge.target)

        # Check verified edges
        if self._edge_verifier is not None:
            blocked_targets: set[str] = set()
            for edge in activated:
                if isinstance(edge, VerifiedEdge) and edge.verifier_id is not None:
                    task = (
                        self._task_factory()
                        if self._task_factory
                        else Task(title="edge_check", description="", verifier_id="")
                    )
                    result = (
                        self._result_factory()
                        if self._result_factory
                        else TaskResult(raw_output="")
                    )
                    if not self._edge_verifier.check_edge(edge, task, result):
                        blocked_targets.add(edge.target)
                        self._skip_subtree(edge.target)

        self._gs.advance_node(node_id, NodeStatus.COMPLETED)

    def _execute_fork_node(self, node_id: str) -> None:
        """Fork simply completes — all outgoing edges activate."""
        self._gs.advance_node(node_id, NodeStatus.RUNNING)

        # Check verified edges
        if self._edge_verifier is not None:
            for edge in self._gs.edges:
                if (
                    edge.source == node_id
                    and isinstance(edge, VerifiedEdge)
                    and edge.verifier_id is not None
                ):
                    task = (
                        self._task_factory()
                        if self._task_factory
                        else Task(title="edge_check", description="", verifier_id="")
                    )
                    result = (
                        self._result_factory()
                        if self._result_factory
                        else TaskResult(raw_output="")
                    )
                    if not self._edge_verifier.check_edge(edge, task, result):
                        self._skip_subtree(edge.target)

        self._gs.advance_node(node_id, NodeStatus.COMPLETED)

    def _execute_join_node(self, node_id: str) -> None:
        """Join node completes when all predecessors are done."""
        self._gs.advance_node(node_id, NodeStatus.RUNNING)
        self._gs.advance_node(node_id, NodeStatus.COMPLETED)

    def _skip_subtree(self, node_id: str) -> None:
        """Recursively mark a node and its descendants as SKIPPED."""
        node = self._gs.nodes[node_id]
        if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
            return
        self._gs.advance_node(node_id, NodeStatus.SKIPPED)
        for edge in self._gs.edges:
            if edge.source == node_id:
                self._skip_subtree(edge.target)

    def _skip_unreachable_in_superstep(self, step: Superstep) -> None:
        """Skip nodes in this superstep that have no completed predecessor."""
        for node_id in step.node_ids:
            node = self._gs.nodes[node_id]
            if node.status != NodeStatus.PENDING:
                continue
            predecessors = [e.source for e in self._gs.edges if e.target == node_id]
            if predecessors and not any(
                self._gs.nodes[p].status == NodeStatus.COMPLETED for p in predecessors
            ):
                # Check edge verification
                incoming_edges = [e for e in self._gs.edges if e.target == node_id]
                all_blocked = True
                for edge in incoming_edges:
                    if isinstance(edge, VerifiedEdge) and edge.verifier_id is not None:
                        if self._edge_verifier is not None:
                            task = (
                                self._task_factory()
                                if self._task_factory
                                else Task(title="", description="", verifier_id="")
                            )
                            result = (
                                self._result_factory()
                                if self._result_factory
                                else TaskResult(raw_output="")
                            )
                            if self._edge_verifier.check_edge(edge, task, result):
                                all_blocked = False
                                break
                    else:
                        all_blocked = False
                        break
                if all_blocked and incoming_edges:
                    self._skip_subtree(node_id)

    def _edge_allows(self, edge: GraphEdge) -> bool:
        """Return True when an incoming edge allows traversal."""
        if not isinstance(edge, VerifiedEdge) or edge.verifier_id is None:
            return True
        if self._edge_verifier is None:
            return True
        task = (
            self._task_factory()
            if self._task_factory
            else Task(title="edge_check", description="", verifier_id="")
        )
        result = self._result_factory() if self._result_factory else TaskResult(raw_output="")
        return self._edge_verifier.check_edge(edge, task, result)

    def _get_ready_nodes_for_execution(self) -> list[str]:
        """Get nodes that are ready based on predecessor status."""
        ready: list[str] = []
        for nid, node in self._gs.nodes.items():
            if node.status not in (NodeStatus.PENDING,):
                continue
            if node.max_iterations > 0 and not self._gs.is_loop_exhausted(nid):
                ready.append(nid)
                continue
            predecessors = [e.source for e in self._gs.edges if e.target == nid]
            if not predecessors:
                ready.append(nid)
            elif node.node_type == NodeType.JOIN:
                if self._gs.is_join_ready(nid):
                    ready.append(nid)
            else:
                if any(self._gs.nodes[pid].status == NodeStatus.COMPLETED for pid in predecessors):
                    ready.append(nid)
        return sorted(ready)

    def _handle_loop_reactivation(self) -> None:
        """Check for completed loop bodies and reactivate loop heads."""
        for edge in self._gs.edges:
            target_node = self._gs.nodes[edge.target]
            source_node = self._gs.nodes[edge.source]

            # A back-edge: completed source, loop-head target
            if (
                target_node.max_iterations > 0
                and source_node.status == NodeStatus.COMPLETED
                and target_node.status == NodeStatus.COMPLETED
                and not self._gs.is_loop_exhausted(edge.target)
            ):
                # Reactivate loop head and body
                self._gs.increment_loop_counter(edge.target)
                if not self._gs.is_loop_exhausted(edge.target):
                    self._gs.advance_node(edge.target, NodeStatus.PENDING)
                    # Reset body nodes too
                    self._reset_loop_body(edge.target)

    def _reset_loop_body(self, loop_head_id: str) -> None:
        """Reset all nodes downstream of a loop head back to PENDING."""
        visited: set[str] = set()
        queue = [loop_head_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for edge in self._gs.edges:
                if edge.source == current:
                    target = self._gs.nodes[edge.target]
                    if target.max_iterations > 0 and edge.target != loop_head_id:
                        continue  # Don't cross into other loops
                    if target.status == NodeStatus.COMPLETED:
                        self._gs.advance_node(edge.target, NodeStatus.PENDING)
                    if edge.target not in visited:
                        queue.append(edge.target)

    # ── Hooks ────────────────────────────────────────────────────────────────

    def _fire_hook(self, event: str, payload: Any = None) -> None:
        """Fire a hook event safely (errors logged, never propagated)."""
        fn = self._hooks.get(event)
        if fn is None:
            return
        try:
            fn(payload)
        except Exception as exc:
            log.warning("graph.hook_error event=%s error=%s", event, exc)
