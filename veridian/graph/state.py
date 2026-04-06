"""
veridian.graph.state
─────────────────────
Graph state model: nodes, edges, topological ordering, cycle detection,
branching (decision), fan-out/fan-in (fork/join), and bounded loops.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from veridian.core.exceptions import GraphError

__all__ = [
    "NodeType",
    "NodeStatus",
    "GraphNode",
    "GraphEdge",
    "GraphState",
]

log = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class NodeType(StrEnum):
    """Type of graph node."""

    TASK = "task"
    DECISION = "decision"
    JOIN = "join"
    FORK = "fork"


class NodeStatus(StrEnum):
    """Execution status of a graph node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class GraphNode:
    """A single node in the execution graph."""

    node_id: str
    node_type: NodeType
    status: NodeStatus = NodeStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 0  # >0 means this node is a loop head

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "status": self.status.value,
            "metadata": self.metadata,
            "max_iterations": self.max_iterations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        return cls(
            node_id=d["node_id"],
            node_type=NodeType(d["node_type"]),
            status=NodeStatus(d["status"]),
            metadata=d.get("metadata", {}),
            max_iterations=d.get("max_iterations", 0),
        )


@dataclass
class GraphEdge:
    """Directed edge connecting two nodes."""

    source: str
    target: str
    condition: Callable[[dict[str, Any]], bool] | None = None
    verifier_id: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "verifier_id": self.verifier_id,
            "label": self.label,
            # condition is a callable — not serializable
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        return cls(
            source=d["source"],
            target=d["target"],
            verifier_id=d.get("verifier_id"),
            label=d.get("label"),
        )


# ── Graph State ──────────────────────────────────────────────────────────────


class GraphState:
    """
    Mutable execution graph with nodes and edges.

    Provides topological sort (with cycle detection that respects loop bounds),
    decision branching, fork/join, and loop counter management.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._loop_counters: dict[str, int] = {}

    # ── Mutation ──────────────────────────────────────────────────────────────

    def add_node(self, node: GraphNode) -> None:
        """Add a node. Raises GraphError if node_id already exists."""
        if node.node_id in self.nodes:
            raise GraphError(f"Node '{node.node_id}' already exists in graph")
        self.nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge. Raises GraphError if source or target node is unknown."""
        if edge.source not in self.nodes:
            raise GraphError(f"Edge source '{edge.source}' not found in graph nodes")
        if edge.target not in self.nodes:
            raise GraphError(f"Edge target '{edge.target}' not found in graph nodes")
        self.edges.append(edge)

    # ── Topological sort ─────────────────────────────────────────────────────

    def topological_sort(self) -> list[str]:
        """
        Kahn's algorithm with loop-bound awareness.

        Back-edges to nodes with ``max_iterations > 0`` are treated as loop
        edges and excluded from the DAG for sorting purposes. True cycles
        (no loop-bounded target) raise ``GraphError``.
        """
        # Build adjacency and in-degree, skipping loop back-edges
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}

        for edge in self.edges:
            target_node = self.nodes[edge.target]
            # If target is a loop head and this is a back-edge, skip
            if target_node.max_iterations > 0 and self._is_back_edge(edge):
                continue
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        queue: deque[str] = deque(sorted(nid for nid, deg in in_degree.items() if deg == 0))
        order: list[str] = []

        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for neighbor in sorted(adj[node_id]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self.nodes):
            raise GraphError(
                "Cycle detected in graph — "
                "use max_iterations on loop-head nodes for intentional loops"
            )
        return order

    def _is_back_edge(self, edge: GraphEdge) -> bool:
        """
        Heuristic: an edge is a back-edge if its target has an outgoing edge
        that eventually reaches the source (i.e., the target was seen
        *before* the source in a forward pass). For simplicity, we check
        if there is ANY forward path from target to source excluding this edge.
        """
        # BFS from target looking for source, excluding the current edge
        visited: set[str] = set()
        queue: deque[str] = deque([edge.target])
        while queue:
            current = queue.popleft()
            if current == edge.source:
                return True
            if current in visited:
                continue
            visited.add(current)
            for e in self.edges:
                if e is edge:
                    continue
                if e.source == current and e.target not in visited:
                    queue.append(e.target)
        return False

    # ── Branching (decision) ─────────────────────────────────────────────────

    def get_activated_edges(self, node_id: str, context: dict[str, Any]) -> list[GraphEdge]:
        """
        Get outgoing edges from ``node_id`` that are activated given context.

        - FORK nodes: all outgoing edges activate (no conditions checked).
        - DECISION nodes: only edges whose condition returns True.
        - Other nodes: all outgoing edges (unconditional).
        """
        outgoing = [e for e in self.edges if e.source == node_id]
        node = self.nodes[node_id]

        if node.node_type == NodeType.DECISION:
            return [e for e in outgoing if e.condition is not None and e.condition(context)]

        # FORK, TASK, JOIN: all outgoing edges
        return outgoing

    # ── Fork / Join ──────────────────────────────────────────────────────────

    def is_join_ready(self, join_node_id: str) -> bool:
        """True if ALL predecessors of the join node are COMPLETED."""
        predecessors = [e.source for e in self.edges if e.target == join_node_id]
        return all(self.nodes[pid].status == NodeStatus.COMPLETED for pid in predecessors)

    # ── Loop counters ────────────────────────────────────────────────────────

    def increment_loop_counter(self, node_id: str) -> None:
        """Increment the loop iteration counter for a node."""
        self._loop_counters[node_id] = self._loop_counters.get(node_id, 0) + 1

    def get_loop_counter(self, node_id: str) -> int:
        """Get the current loop iteration count for a node."""
        return self._loop_counters.get(node_id, 0)

    def is_loop_exhausted(self, node_id: str) -> bool:
        """True if the node has reached its max_iterations."""
        node = self.nodes[node_id]
        if node.max_iterations <= 0:
            return False
        return self.get_loop_counter(node_id) >= node.max_iterations

    # ── Ready nodes ──────────────────────────────────────────────────────────

    def get_ready_nodes(self, context: dict[str, Any] | None = None) -> list[str]:
        """
        Return node IDs that are PENDING and whose predecessors are all COMPLETED.
        Respects join semantics (all predecessors required).
        """
        _ = context
        ready: list[str] = []
        for nid, node in self.nodes.items():
            if node.status != NodeStatus.PENDING:
                continue
            predecessors = [e.source for e in self.edges if e.target == nid]
            if not predecessors:
                ready.append(nid)
            elif node.node_type == NodeType.JOIN:
                if self.is_join_ready(nid):
                    ready.append(nid)
            else:
                # At least one predecessor must be completed
                if any(self.nodes[pid].status == NodeStatus.COMPLETED for pid in predecessors):
                    ready.append(nid)
        return sorted(ready)

    # ── Node advancement ─────────────────────────────────────────────────────

    def advance_node(self, node_id: str, new_status: NodeStatus) -> None:
        """Set a node's status."""
        if node_id not in self.nodes:
            raise GraphError(f"Node '{node_id}' not found in graph")
        self.nodes[node_id].status = new_status

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "loop_counters": dict(self._loop_counters),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphState:
        gs = cls()
        for _nid, nd in d["nodes"].items():
            gs.nodes[nd["node_id"]] = GraphNode.from_dict(nd)
        for ed in d["edges"]:
            gs.edges.append(GraphEdge.from_dict(ed))
        gs._loop_counters = dict(d.get("loop_counters", {}))
        return gs
