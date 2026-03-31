"""
veridian.knowledge.graph
────────────────────────
NetworkX-based regulatory knowledge graph.

Supports:
- Add/get nodes and edges
- Query verifier suggestions for a given article/requirement
- Path finding between nodes
- Natural-language query interface (keyword matching)
"""

from __future__ import annotations

import logging

import networkx as nx

from veridian.core.exceptions import KnowledgeGraphError
from veridian.knowledge.models import EdgeType, NodeType, RegEdge, RegNode

log = logging.getLogger(__name__)


class RegulatoryGraph:
    """
    NetworkX-backed knowledge graph of regulatory requirements.

    Nodes carry RegNode data; edges carry RegEdge data.
    All structural queries (suggest_verifiers, path) use the underlying
    DiGraph directly — no external LLM calls.
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()

    # ── Mutation ───────────────────────────────────────────────────────────────

    def add_node(self, node: RegNode) -> None:
        """Add a RegNode to the graph. Overwrites if id already exists."""
        self._graph.add_node(node.id, data=node)

    def add_edge(self, edge: RegEdge) -> None:
        """Add a directed RegEdge. Both source and target must already exist."""
        if edge.source not in self._graph:
            raise KnowledgeGraphError(
                f"Source node '{edge.source}' not in graph. Add it first."
            )
        if edge.target not in self._graph:
            raise KnowledgeGraphError(
                f"Target node '{edge.target}' not in graph. Add it first."
            )
        self._graph.add_edge(
            edge.source,
            edge.target,
            data=edge,
        )

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> RegNode:
        """Return RegNode by id. Raises KnowledgeGraphError if missing."""
        if node_id not in self._graph:
            raise KnowledgeGraphError(
                f"Node '{node_id}' not found in the knowledge graph. "
                f"Available node count: {self.node_count}."
            )
        return self._graph.nodes[node_id]["data"]

    def get_edges(self, node_id: str) -> list[RegEdge]:
        """Return all outgoing RegEdges from node_id."""
        if node_id not in self._graph:
            raise KnowledgeGraphError(f"Node '{node_id}' not found.")
        return [
            self._graph.edges[node_id, target]["data"]
            for target in self._graph.successors(node_id)
        ]

    def get_nodes_by_type(self, node_type: NodeType) -> list[RegNode]:
        """Return all nodes matching node_type."""
        return [
            self._graph.nodes[nid]["data"]
            for nid in self._graph.nodes
            if self._graph.nodes[nid]["data"].node_type == node_type
        ]

    # ── Queries ────────────────────────────────────────────────────────────────

    def suggest_verifiers(self, node_id: str) -> list[str]:
        """
        Return list of verifier node IDs reachable from node_id via
        IMPLEMENTS edges. Includes both direct and transitive verifiers.
        """
        if node_id not in self._graph:
            raise KnowledgeGraphError(f"Node '{node_id}' not found.")
        verifiers: list[str] = []
        for target in self._graph.successors(node_id):
            edge_data: RegEdge = self._graph.edges[node_id, target]["data"]
            target_node: RegNode = self._graph.nodes[target]["data"]
            if (
                edge_data.edge_type == EdgeType.IMPLEMENTS
                and target_node.node_type == NodeType.VERIFIER
            ):
                verifiers.append(target)
        return verifiers

    def path(self, source: str, target: str) -> list[str] | None:
        """
        Return shortest path from source to target as a list of node IDs,
        or None if no path exists.
        """
        try:
            return nx.shortest_path(self._graph, source=source, target=target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def query(self, question: str) -> str:
        """
        Simple keyword-matching query interface.

        Extracts node IDs mentioned in the question and returns a description
        of those nodes plus their suggested verifiers.

        Example: "What verifiers do I need for eu_ai_act_art_9?"
        """
        # Find node IDs mentioned in the question
        mentioned: list[str] = []
        for nid in self._graph.nodes:
            # Match the node id directly or its label keywords
            node: RegNode = self._graph.nodes[nid]["data"]
            if nid.lower() in question.lower() or node.label.lower() in question.lower():
                mentioned.append(nid)

        if not mentioned:
            return (
                f"No matching nodes found for query: {question!r}. "
                f"Available node types: {', '.join(nt.value for nt in NodeType)}."
            )

        lines: list[str] = []
        for nid in mentioned:
            node = self.get_node(nid)
            lines.append(f"Node: {nid} ({node.label})")
            if node.description:
                lines.append(f"  Description: {node.description}")
            verifiers = self.suggest_verifiers(nid)
            if verifiers:
                lines.append(f"  Recommended verifiers: {', '.join(verifiers)}")
            else:
                lines.append("  Recommended verifiers: none mapped")
            # Also show outgoing edge types
            edges = self.get_edges(nid)
            if edges:
                for e in edges:
                    target_node = self.get_node(e.target)
                    lines.append(f"  --[{e.edge_type}]--> {e.target} ({target_node.label})")

        return "\n".join(lines)

    # ── Stats ──────────────────────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def __repr__(self) -> str:
        return (
            f"RegulatoryGraph(nodes={self.node_count}, edges={self.edge_count})"
        )


__all__ = ["RegulatoryGraph"]
