"""
tests.unit.test_graph_state
────────────────────────────
Unit tests for GraphNode, GraphEdge, GraphState, NodeType, NodeStatus.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from veridian.core.exceptions import GraphError
from veridian.graph.state import (
    GraphEdge,
    GraphNode,
    GraphState,
    NodeStatus,
    NodeType,
)

# ── NodeType enum ────────────────────────────────────────────────────────────


class TestNodeType:
    def test_task_type_exists(self) -> None:
        assert NodeType.TASK.value == "task"

    def test_decision_type_exists(self) -> None:
        assert NodeType.DECISION.value == "decision"

    def test_join_type_exists(self) -> None:
        assert NodeType.JOIN.value == "join"

    def test_fork_type_exists(self) -> None:
        assert NodeType.FORK.value == "fork"


# ── NodeStatus enum ──────────────────────────────────────────────────────────


class TestNodeStatus:
    def test_pending(self) -> None:
        assert NodeStatus.PENDING.value == "pending"

    def test_running(self) -> None:
        assert NodeStatus.RUNNING.value == "running"

    def test_completed(self) -> None:
        assert NodeStatus.COMPLETED.value == "completed"

    def test_failed(self) -> None:
        assert NodeStatus.FAILED.value == "failed"

    def test_skipped(self) -> None:
        assert NodeStatus.SKIPPED.value == "skipped"


# ── GraphNode creation ───────────────────────────────────────────────────────


class TestGraphNode:
    def test_create_task_node(self) -> None:
        node = GraphNode(node_id="n1", node_type=NodeType.TASK)
        assert node.node_id == "n1"
        assert node.node_type == NodeType.TASK
        assert node.status == NodeStatus.PENDING
        assert node.metadata == {}
        assert node.max_iterations == 0

    def test_create_decision_node(self) -> None:
        node = GraphNode(node_id="d1", node_type=NodeType.DECISION)
        assert node.node_type == NodeType.DECISION

    def test_create_join_node(self) -> None:
        node = GraphNode(node_id="j1", node_type=NodeType.JOIN)
        assert node.node_type == NodeType.JOIN

    def test_create_fork_node(self) -> None:
        node = GraphNode(node_id="f1", node_type=NodeType.FORK)
        assert node.node_type == NodeType.FORK

    def test_node_with_metadata(self) -> None:
        node = GraphNode(
            node_id="n1",
            node_type=NodeType.TASK,
            metadata={"key": "value"},
        )
        assert node.metadata == {"key": "value"}

    def test_node_with_max_iterations(self) -> None:
        node = GraphNode(
            node_id="n1",
            node_type=NodeType.TASK,
            max_iterations=5,
        )
        assert node.max_iterations == 5


# ── GraphEdge ────────────────────────────────────────────────────────────────


class TestGraphEdge:
    def test_create_simple_edge(self) -> None:
        edge = GraphEdge(source="a", target="b")
        assert edge.source == "a"
        assert edge.target == "b"
        assert edge.condition is None
        assert edge.verifier_id is None

    def test_edge_with_condition(self) -> None:
        cond = lambda ctx: ctx.get("approved", False)  # noqa: E731
        edge = GraphEdge(source="a", target="b", condition=cond)
        assert edge.condition is not None
        assert edge.condition({"approved": True}) is True
        assert edge.condition({"approved": False}) is False

    def test_edge_with_verifier_id(self) -> None:
        edge = GraphEdge(source="a", target="b", verifier_id="bash_exit")
        assert edge.verifier_id == "bash_exit"

    def test_edge_with_label(self) -> None:
        edge = GraphEdge(source="a", target="b", label="on_success")
        assert edge.label == "on_success"


# ── GraphState: add nodes / edges ────────────────────────────────────────────


class TestGraphStateBasic:
    def test_add_node(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        assert "a" in gs.nodes
        assert gs.nodes["a"].node_type == NodeType.TASK

    def test_add_duplicate_node_raises(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        with pytest.raises(GraphError, match="already exists"):
            gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))

    def test_add_edge(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        assert len(gs.edges) == 1

    def test_add_edge_unknown_source_raises(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))
        with pytest.raises(GraphError, match="source"):
            gs.add_edge(GraphEdge(source="a", target="b"))

    def test_add_edge_unknown_target_raises(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK))
        with pytest.raises(GraphError, match="target"):
            gs.add_edge(GraphEdge(source="a", target="b"))


# ── GraphState: topological sort ─────────────────────────────────────────────


class TestTopologicalSort:
    def test_linear_sort(self) -> None:
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="c"))
        order = gs.topological_sort()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond_sort(self) -> None:
        gs = GraphState()
        for nid in ("a", "b", "c", "d"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="a", target="c"))
        gs.add_edge(GraphEdge(source="b", target="d"))
        gs.add_edge(GraphEdge(source="c", target="d"))
        order = gs.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_detection(self) -> None:
        gs = GraphState()
        for nid in ("a", "b"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="a"))
        with pytest.raises(GraphError, match="[Cc]ycle"):
            gs.topological_sort()

    def test_cycle_with_loop_bound_allowed(self) -> None:
        """Back-edges to nodes with max_iterations > 0 are loops, not cycles."""
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK, max_iterations=3))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="a"))
        # Should succeed: back-edge is a loop with bounded iterations
        order = gs.topological_sort()
        assert "a" in order
        assert "b" in order


# ── Branching: decision nodes ────────────────────────────────────────────────


class TestBranching:
    def test_decision_routes_to_correct_branch(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="start", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="decide", node_type=NodeType.DECISION))
        gs.add_node(GraphNode(node_id="branch_a", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="branch_b", node_type=NodeType.TASK))

        gs.add_edge(GraphEdge(source="start", target="decide"))
        gs.add_edge(
            GraphEdge(
                source="decide",
                target="branch_a",
                condition=lambda ctx: ctx.get("path") == "a",
                label="path_a",
            )
        )
        gs.add_edge(
            GraphEdge(
                source="decide",
                target="branch_b",
                condition=lambda ctx: ctx.get("path") == "b",
                label="path_b",
            )
        )

        ctx: dict[str, Any] = {"path": "a"}
        activated = gs.get_activated_edges("decide", ctx)
        targets = [e.target for e in activated]
        assert "branch_a" in targets
        assert "branch_b" not in targets

    def test_decision_no_match(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="decide", node_type=NodeType.DECISION))
        gs.add_node(GraphNode(node_id="branch_a", node_type=NodeType.TASK))
        gs.add_edge(
            GraphEdge(
                source="decide",
                target="branch_a",
                condition=lambda ctx: False,
            )
        )
        activated = gs.get_activated_edges("decide", {})
        assert len(activated) == 0


# ── Fan-out / Fan-in: fork / join ────────────────────────────────────────────


class TestForkJoin:
    def test_fork_spawns_parallel_paths(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="start", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="fork", node_type=NodeType.FORK))
        gs.add_node(GraphNode(node_id="p1", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="p2", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="join", node_type=NodeType.JOIN))

        gs.add_edge(GraphEdge(source="start", target="fork"))
        gs.add_edge(GraphEdge(source="fork", target="p1"))
        gs.add_edge(GraphEdge(source="fork", target="p2"))
        gs.add_edge(GraphEdge(source="p1", target="join"))
        gs.add_edge(GraphEdge(source="p2", target="join"))

        # Fork should activate all outgoing edges unconditionally
        activated = gs.get_activated_edges("fork", {})
        targets = {e.target for e in activated}
        assert targets == {"p1", "p2"}

    def test_join_waits_for_all_predecessors(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="p1", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="p2", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="join", node_type=NodeType.JOIN))
        gs.add_edge(GraphEdge(source="p1", target="join"))
        gs.add_edge(GraphEdge(source="p2", target="join"))

        # Only p1 complete -> join NOT ready
        gs.nodes["p1"].status = NodeStatus.COMPLETED
        assert not gs.is_join_ready("join")

        # Both complete -> join ready
        gs.nodes["p2"].status = NodeStatus.COMPLETED
        assert gs.is_join_ready("join")


# ── Loops: back-edges with max_iterations ────────────────────────────────────


class TestLoops:
    def test_loop_counter_tracks_iterations(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="loop_start", node_type=NodeType.TASK, max_iterations=3))
        gs.add_node(GraphNode(node_id="loop_body", node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="loop_start", target="loop_body"))
        gs.add_edge(GraphEdge(source="loop_body", target="loop_start"))

        # Increment loop counter
        for i in range(3):
            gs.increment_loop_counter("loop_start")
            assert gs.get_loop_counter("loop_start") == i + 1

    def test_loop_max_exceeded_detected(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="loop_start", node_type=NodeType.TASK, max_iterations=2))
        gs.increment_loop_counter("loop_start")
        gs.increment_loop_counter("loop_start")
        assert gs.is_loop_exhausted("loop_start")

    def test_loop_not_exhausted_within_bounds(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="loop_start", node_type=NodeType.TASK, max_iterations=5))
        gs.increment_loop_counter("loop_start")
        assert not gs.is_loop_exhausted("loop_start")


# ── Serialization: to_dict / from_dict ───────────────────────────────────────


class TestSerialization:
    def _build_graph(self) -> GraphState:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK, metadata={"k": "v"}))
        gs.add_node(GraphNode(node_id="b", node_type=NodeType.DECISION))
        gs.add_node(GraphNode(node_id="c", node_type=NodeType.JOIN))
        gs.add_edge(GraphEdge(source="a", target="b", label="first"))
        gs.add_edge(GraphEdge(source="b", target="c", verifier_id="bash_exit"))
        return gs

    def test_to_dict_returns_dict(self) -> None:
        gs = self._build_graph()
        d = gs.to_dict()
        assert isinstance(d, dict)
        assert "nodes" in d
        assert "edges" in d

    def test_roundtrip(self) -> None:
        gs = self._build_graph()
        d = gs.to_dict()
        # Ensure JSON-serializable
        raw = json.dumps(d)
        restored = GraphState.from_dict(json.loads(raw))
        assert set(restored.nodes.keys()) == {"a", "b", "c"}
        assert len(restored.edges) == 2
        assert restored.nodes["a"].metadata == {"k": "v"}
        assert restored.nodes["b"].node_type == NodeType.DECISION

    def test_roundtrip_preserves_status(self) -> None:
        gs = self._build_graph()
        gs.nodes["a"].status = NodeStatus.COMPLETED
        d = gs.to_dict()
        restored = GraphState.from_dict(d)
        assert restored.nodes["a"].status == NodeStatus.COMPLETED

    def test_roundtrip_preserves_loop_counters(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="a", node_type=NodeType.TASK, max_iterations=3))
        gs.increment_loop_counter("a")
        gs.increment_loop_counter("a")
        d = gs.to_dict()
        restored = GraphState.from_dict(d)
        assert restored.get_loop_counter("a") == 2

    def test_roundtrip_preserves_verifier_id(self) -> None:
        gs = self._build_graph()
        d = gs.to_dict()
        restored = GraphState.from_dict(d)
        edge_bc = [e for e in restored.edges if e.source == "b" and e.target == "c"][0]
        assert edge_bc.verifier_id == "bash_exit"

    def test_roundtrip_preserves_edge_label(self) -> None:
        gs = self._build_graph()
        d = gs.to_dict()
        restored = GraphState.from_dict(d)
        edge_ab = [e for e in restored.edges if e.source == "a" and e.target == "b"][0]
        assert edge_ab.label == "first"
