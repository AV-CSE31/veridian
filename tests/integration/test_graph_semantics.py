"""
tests.integration.test_graph_semantics
─────────────────────────────────────────
Integration tests for end-to-end graph execution semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from veridian.core.task import Task, TaskResult
from veridian.graph.executor import GraphExecutor
from veridian.graph.state import GraphEdge, GraphNode, GraphState, NodeStatus, NodeType
from veridian.graph.superstep import SuperstepScheduler
from veridian.graph.verified_edge import EdgeVerifier, VerifiedEdge
from veridian.verify.base import BaseVerifier, VerificationResult

# ── Helpers ──────────────────────────────────────────────────────────────────


class _AlwaysPassVerifier(BaseVerifier):
    id: ClassVar[str] = "_integ_pass"
    description: ClassVar[str] = "Always passes"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class _AlwaysFailVerifier(BaseVerifier):
    id: ClassVar[str] = "_integ_fail"
    description: ClassVar[str] = "Always fails"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="integration failure")


def _make_task() -> Task:
    return Task(title="t", description="d", verifier_id="_integ_pass")


def _make_result() -> TaskResult:
    return TaskResult(raw_output="ok")


# Callback that simply marks nodes completed
def _auto_complete_callback(node_id: str, gs: GraphState) -> TaskResult:
    """Simulates a task executor that always succeeds."""
    return TaskResult(raw_output=f"completed {node_id}")


# ── Linear graph: A → B → C ─────────────────────────────────────────────────


class TestLinearExecution:
    def test_linear_graph_completes_all_nodes(self) -> None:
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="c"))

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
        )
        executor.execute()

        for nid in ("a", "b", "c"):
            assert gs.nodes[nid].status == NodeStatus.COMPLETED


# ── Branching graph: A → decision → B or C ──────────────────────────────────


class TestBranchingExecution:
    def test_decision_routes_correctly(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="decide", node_type=NodeType.DECISION))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="c", node_type=NodeType.TASK))

        gs.add_edge(GraphEdge(source="a", target="decide"))
        gs.add_edge(
            GraphEdge(
                source="decide",
                target="b",
                condition=lambda ctx: ctx.get("choice") == "b",
                label="go_b",
            )
        )
        gs.add_edge(
            GraphEdge(
                source="decide",
                target="c",
                condition=lambda ctx: ctx.get("choice") == "c",
                label="go_c",
            )
        )

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
            context={"choice": "b"},
        )
        executor.execute()

        assert gs.nodes["a"].status == NodeStatus.COMPLETED
        assert gs.nodes["decide"].status == NodeStatus.COMPLETED
        assert gs.nodes["b"].status == NodeStatus.COMPLETED
        assert gs.nodes["c"].status == NodeStatus.SKIPPED


# ── Loop graph: A → B → A (max_iterations=3) ────────────────────────────────


class TestLoopExecution:
    def test_loop_runs_max_iterations(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK, max_iterations=3))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))

        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="a"))

        call_counts: dict[str, int] = {"a": 0, "b": 0}

        def counting_callback(node_id: str, graph_state: GraphState) -> TaskResult:
            call_counts[node_id] = call_counts.get(node_id, 0) + 1
            return TaskResult(raw_output=f"iter {call_counts[node_id]}")

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=counting_callback,
        )
        executor.execute()

        # a should have been called 3 times (max_iterations)
        assert call_counts["a"] == 3
        assert call_counts["b"] == 3


# ── Fan-out / Fan-in: A → fork → [B,C] → join → D ──────────────────────────


class TestFanOutFanIn:
    def test_fork_join_all_paths(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="fork", node_type=NodeType.FORK))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="c", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="join", node_type=NodeType.JOIN))
        gs.add_node(GraphNode(node_id="d", node_type=NodeType.TASK))

        gs.add_edge(GraphEdge(source="a", target="fork"))
        gs.add_edge(GraphEdge(source="fork", target="b"))
        gs.add_edge(GraphEdge(source="fork", target="c"))
        gs.add_edge(GraphEdge(source="b", target="join"))
        gs.add_edge(GraphEdge(source="c", target="join"))
        gs.add_edge(GraphEdge(source="join", target="d"))

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
        )
        executor.execute()

        for nid in ("a", "fork", "b", "c", "join", "d"):
            assert gs.nodes[nid].status == NodeStatus.COMPLETED


# ── Graph with verified edges ────────────────────────────────────────────────


class TestVerifiedEdgeExecution:
    def test_verified_edge_blocks_on_failure(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))

        gs.add_edge(VerifiedEdge(source="a", target="b", verifier_id="_integ_fail"))

        registry: dict[str, BaseVerifier] = {"_integ_fail": _AlwaysFailVerifier()}
        edge_verifier = EdgeVerifier(verifier_lookup=registry)

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
            edge_verifier=edge_verifier,
            task_factory=_make_task,
            result_factory=_make_result,
        )
        executor.execute()

        assert gs.nodes["a"].status == NodeStatus.COMPLETED
        # b should NOT be completed because the edge verification failed
        assert gs.nodes["b"].status != NodeStatus.COMPLETED

    def test_verified_edge_allows_on_pass(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))

        gs.add_edge(VerifiedEdge(source="a", target="b", verifier_id="_integ_pass"))

        registry: dict[str, BaseVerifier] = {"_integ_pass": _AlwaysPassVerifier()}
        edge_verifier = EdgeVerifier(verifier_lookup=registry)

        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
            edge_verifier=edge_verifier,
            task_factory=_make_task,
            result_factory=_make_result,
        )
        executor.execute()

        assert gs.nodes["a"].status == NodeStatus.COMPLETED
        assert gs.nodes["b"].status == NodeStatus.COMPLETED


# ── Checkpoint / Resume at superstep boundary ────────────────────────────────


class TestCheckpointResume:
    def test_checkpoint_and_resume(self, tmp_path: Path) -> None:
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="c"))

        # Execute first superstep, then checkpoint
        executor = GraphExecutor(
            graph_state=gs,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
            checkpoint_dir=tmp_path,
        )
        # Execute only first superstep
        executor.execute_one_superstep()
        assert gs.nodes["a"].status == NodeStatus.COMPLETED
        assert gs.nodes["b"].status == NodeStatus.PENDING

        # Save checkpoint
        cp_path = executor.save_checkpoint()
        assert cp_path.exists()

        # Create new graph state from checkpoint
        gs2 = GraphState()
        for nid in ("a", "b", "c"):
            gs2.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs2.add_edge(GraphEdge(source="a", target="b"))
        gs2.add_edge(GraphEdge(source="b", target="c"))

        executor2 = GraphExecutor(
            graph_state=gs2,
            scheduler=SuperstepScheduler(),
            node_callback=_auto_complete_callback,
            checkpoint_dir=tmp_path,
        )
        executor2.restore_checkpoint(cp_path)

        # a should already be completed from checkpoint
        assert gs2.nodes["a"].status == NodeStatus.COMPLETED

        # Execute remaining
        executor2.execute()
        for nid in ("a", "b", "c"):
            assert gs2.nodes[nid].status == NodeStatus.COMPLETED
