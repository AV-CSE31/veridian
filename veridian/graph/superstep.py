"""
veridian.graph.superstep
─────────────────────────
BSP (Bulk Synchronous Parallel) superstep scheduling.

Nodes are grouped into topological levels (supersteps). All nodes in a
superstep must reach COMPLETED before the next superstep activates.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field

from veridian.core.exceptions import GraphError
from veridian.graph.state import GraphState, NodeStatus

__all__ = [
    "Superstep",
    "SuperstepScheduler",
]

log = logging.getLogger(__name__)


@dataclass
class Superstep:
    """A group of nodes that can execute in parallel within a barrier."""

    step_number: int
    node_ids: list[str] = field(default_factory=list)


class SuperstepScheduler:
    """
    Computes superstep assignment from graph topology using topological levels.

    All nodes at the same topological depth belong to the same superstep.
    Nodes within a superstep are sorted by ``node_id`` for determinism.
    """

    def compute_supersteps(self, graph_state: GraphState) -> list[Superstep]:
        """
        Partition graph nodes into ordered supersteps based on topological levels.

        Returns an empty list for empty graphs.
        Raises GraphError if the graph contains unresolvable cycles.
        """
        if not graph_state.nodes:
            return []

        # Build adjacency and in-degree (respecting loop back-edges)
        in_degree: dict[str, int] = {nid: 0 for nid in graph_state.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in graph_state.nodes}

        for edge in graph_state.edges:
            target_node = graph_state.nodes[edge.target]
            if target_node.max_iterations > 0 and graph_state._is_back_edge(edge):
                continue
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        # BFS level computation
        queue: deque[str] = deque(sorted(nid for nid, deg in in_degree.items() if deg == 0))
        levels: dict[str, int] = {}
        for nid in queue:
            levels[nid] = 0

        while queue:
            current = queue.popleft()
            for neighbor in sorted(adj[current]):
                in_degree[neighbor] -= 1
                new_level = levels[current] + 1
                levels[neighbor] = max(levels.get(neighbor, 0), new_level)
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(levels) != len(graph_state.nodes):
            raise GraphError("Cannot compute supersteps — graph contains unresolvable cycles")

        # Group by level
        level_groups: dict[int, list[str]] = defaultdict(list)
        for nid, level in levels.items():
            level_groups[level].append(nid)

        supersteps: list[Superstep] = []
        for step_num in sorted(level_groups.keys()):
            supersteps.append(
                Superstep(
                    step_number=step_num,
                    node_ids=sorted(level_groups[step_num]),
                )
            )

        log.debug(
            "superstep.computed steps=%d nodes=%d",
            len(supersteps),
            len(graph_state.nodes),
        )
        return supersteps

    def is_superstep_complete(self, graph_state: GraphState, superstep: Superstep) -> bool:
        """True if all nodes in the superstep are COMPLETED."""
        return all(
            graph_state.nodes[nid].status == NodeStatus.COMPLETED for nid in superstep.node_ids
        )

    def get_current_superstep(
        self,
        graph_state: GraphState,
        supersteps: list[Superstep],
    ) -> Superstep | None:
        """Return the first non-complete superstep, or None if all done."""
        for step in supersteps:
            if not self.is_superstep_complete(graph_state, step):
                return step
        return None
