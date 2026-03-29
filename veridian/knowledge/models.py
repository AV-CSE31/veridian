"""
veridian.knowledge.models
─────────────────────────
Data models for the regulatory knowledge graph.

Nodes represent: regulations, articles, requirements, verifier mappings.
Edges represent: requires, implements, supersedes, references relationships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    REGULATION = "regulation"
    ARTICLE = "article"
    REQUIREMENT = "requirement"
    VERIFIER = "verifier"


class EdgeType(StrEnum):
    REQUIRES = "requires"
    IMPLEMENTS = "implements"
    SUPERSEDES = "supersedes"
    REFERENCES = "references"


@dataclass
class RegNode:
    """A node in the regulatory knowledge graph."""

    id: str
    label: str
    node_type: NodeType
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type.value,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegNode:
        return cls(
            id=d["id"],
            label=d["label"],
            node_type=NodeType(d["node_type"]),
            description=d.get("description", ""),
            metadata=d.get("metadata", {}),
        )


@dataclass
class RegEdge:
    """A directed edge in the regulatory knowledge graph."""

    source: str
    target: str
    edge_type: EdgeType
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type.value,
            "weight": self.weight,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegEdge:
        return cls(
            source=d["source"],
            target=d["target"],
            edge_type=EdgeType(d["edge_type"]),
            weight=d.get("weight", 1.0),
            metadata=d.get("metadata", {}),
        )


__all__ = ["NodeType", "EdgeType", "RegNode", "RegEdge"]
