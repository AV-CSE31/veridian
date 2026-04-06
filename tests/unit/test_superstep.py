"""
tests.unit.test_superstep
──────────────────────────
Unit tests for Superstep assignment and barrier enforcement.
"""

from __future__ import annotations

from veridian.graph.state import GraphEdge, GraphNode, GraphState, NodeStatus, NodeType
from veridian.graph.superstep import Superstep, SuperstepScheduler

# ── Superstep dataclass ──────────────────────────────────────────────────────


class TestSuperstep:
    def test_creation(self) -> None:
        s = Superstep(step_number=0, node_ids=["a", "b"])
        assert s.step_number == 0
        assert s.node_ids == ["a", "b"]

    def test_empty_superstep(self) -> None:
        s = Superstep(step_number=1, node_ids=[])
        assert len(s.node_ids) == 0


# ── SuperstepScheduler: compute_supersteps ───────────────────────────────────


class TestSuperstepScheduler:
    def test_linear_chain_three_supersteps(self) -> None:
        """A→B→C should give 3 supersteps, one per node."""
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="b", target="c"))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 3
        assert steps[0].node_ids == ["a"]
        assert steps[1].node_ids == ["b"]
        assert steps[2].node_ids == ["c"]

    def test_parallel_nodes_in_same_superstep(self) -> None:
        """Independent nodes share a superstep."""
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        # b and c both depend only on a
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="a", target="c"))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 2
        assert steps[0].node_ids == ["a"]
        assert set(steps[1].node_ids) == {"b", "c"}

    def test_diamond_graph(self) -> None:
        """A → (B, C) → D gives 3 supersteps."""
        gs = GraphState()
        for nid in ("a", "b", "c", "d"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="a", target="c"))
        gs.add_edge(GraphEdge(source="b", target="d"))
        gs.add_edge(GraphEdge(source="c", target="d"))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 3
        assert steps[0].node_ids == ["a"]
        assert set(steps[1].node_ids) == {"b", "c"}
        assert steps[2].node_ids == ["d"]

    def test_deterministic_ordering_within_superstep(self) -> None:
        """Nodes within a superstep are sorted by node_id for determinism."""
        gs = GraphState()
        for nid in ("z", "a", "m", "b"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        # All independent nodes in one superstep, sorted
        assert len(steps) == 1
        assert steps[0].node_ids == ["a", "b", "m", "z"]

    def test_fork_join_pattern(self) -> None:
        """
        start → fork → (p1, p2) → join → end
        Should give 5 supersteps.
        """
        gs = GraphState()
        gs.add_node(GraphNode(node_id="start", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="fork", node_type=NodeType.FORK))
        gs.add_node(GraphNode(node_id="p1", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="p2", node_type=NodeType.TASK))
        gs.add_node(GraphNode(node_id="join", node_type=NodeType.JOIN))
        gs.add_node(GraphNode(node_id="end", node_type=NodeType.TASK))

        gs.add_edge(GraphEdge(source="start", target="fork"))
        gs.add_edge(GraphEdge(source="fork", target="p1"))
        gs.add_edge(GraphEdge(source="fork", target="p2"))
        gs.add_edge(GraphEdge(source="p1", target="join"))
        gs.add_edge(GraphEdge(source="p2", target="join"))
        gs.add_edge(GraphEdge(source="join", target="end"))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 5
        assert steps[0].node_ids == ["start"]
        assert steps[1].node_ids == ["fork"]
        assert set(steps[2].node_ids) == {"p1", "p2"}
        assert steps[3].node_ids == ["join"]
        assert steps[4].node_ids == ["end"]

    def test_barrier_enforcement(self) -> None:
        """All nodes in step N must be COMPLETED before step N+1 activates."""
        gs = GraphState()
        for nid in ("a", "b", "c"):
            gs.add_node(GraphNode(node_id=nid, node_type=NodeType.TASK))
        gs.add_edge(GraphEdge(source="a", target="b"))
        gs.add_edge(GraphEdge(source="a", target="c"))

        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)

        # Step 0 not complete
        assert not scheduler.is_superstep_complete(gs, steps[0])

        # Complete step 0
        gs.nodes["a"].status = NodeStatus.COMPLETED
        assert scheduler.is_superstep_complete(gs, steps[0])

        # Step 1 has b and c, not complete yet
        assert not scheduler.is_superstep_complete(gs, steps[1])

        # Complete both
        gs.nodes["b"].status = NodeStatus.COMPLETED
        gs.nodes["c"].status = NodeStatus.COMPLETED
        assert scheduler.is_superstep_complete(gs, steps[1])

    def test_single_node_graph(self) -> None:
        gs = GraphState()
        gs.add_node(GraphNode(node_id="only", node_type=NodeType.TASK))
        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 1
        assert steps[0].node_ids == ["only"]

    def test_empty_graph(self) -> None:
        gs = GraphState()
        scheduler = SuperstepScheduler()
        steps = scheduler.compute_supersteps(gs)
        assert len(steps) == 0
