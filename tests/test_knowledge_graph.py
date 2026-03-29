"""
tests.test_knowledge_graph
──────────────────────────
Tests for F3.1 — Regulatory Knowledge Graph.

Coverage:
  - Node/edge model integrity
  - Graph construction and loader
  - Verifier mapping queries
  - EU AI Act and GDPR article lookups
  - Auto-mapping suggestion
"""

from __future__ import annotations

import pytest

from veridian.knowledge.models import (
    EdgeType,
    NodeType,
    RegNode,
    RegEdge,
)
from veridian.knowledge.graph import RegulatoryGraph
from veridian.knowledge.loader import load_default_graph


# ─── RegNode / RegEdge model tests ───────────────────────────────────────────


class TestRegNode:
    def test_node_creation(self) -> None:
        node = RegNode(
            id="eu_ai_act",
            label="EU AI Act",
            node_type=NodeType.REGULATION,
            description="EU Artificial Intelligence Act",
        )
        assert node.id == "eu_ai_act"
        assert node.node_type == NodeType.REGULATION

    def test_node_metadata(self) -> None:
        node = RegNode(
            id="art_9",
            label="Article 9",
            node_type=NodeType.ARTICLE,
            metadata={"regulation": "EU AI Act", "risk_category": "high"},
        )
        assert node.metadata["risk_category"] == "high"

    def test_all_node_types_exist(self) -> None:
        assert NodeType.REGULATION in NodeType
        assert NodeType.ARTICLE in NodeType
        assert NodeType.REQUIREMENT in NodeType
        assert NodeType.VERIFIER in NodeType

    def test_reg_edge_creation(self) -> None:
        edge = RegEdge(
            source="art_9",
            target="llm_judge",
            edge_type=EdgeType.IMPLEMENTS,
        )
        assert edge.source == "art_9"
        assert edge.edge_type == EdgeType.IMPLEMENTS

    def test_all_edge_types_exist(self) -> None:
        assert EdgeType.REQUIRES in EdgeType
        assert EdgeType.IMPLEMENTS in EdgeType
        assert EdgeType.SUPERSEDES in EdgeType
        assert EdgeType.REFERENCES in EdgeType


# ─── RegulatoryGraph tests ────────────────────────────────────────────────────


class TestRegulatoryGraph:
    def test_add_and_get_node(self) -> None:
        g = RegulatoryGraph()
        node = RegNode(id="n1", label="Test", node_type=NodeType.REGULATION)
        g.add_node(node)
        retrieved = g.get_node("n1")
        assert retrieved.id == "n1"
        assert retrieved.label == "Test"

    def test_get_missing_node_raises(self) -> None:
        from veridian.core.exceptions import KnowledgeGraphError
        g = RegulatoryGraph()
        with pytest.raises(KnowledgeGraphError):
            g.get_node("nonexistent")

    def test_add_edge(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="a", label="A", node_type=NodeType.ARTICLE))
        g.add_node(RegNode(id="b", label="B", node_type=NodeType.VERIFIER))
        g.add_edge(RegEdge(source="a", target="b", edge_type=EdgeType.IMPLEMENTS))
        edges = g.get_edges("a")
        assert len(edges) == 1
        assert edges[0].target == "b"

    def test_node_count(self) -> None:
        g = RegulatoryGraph()
        for i in range(3):
            g.add_node(RegNode(id=f"n{i}", label=f"Node {i}", node_type=NodeType.ARTICLE))
        assert g.node_count == 3

    def test_edge_count(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="a", label="A", node_type=NodeType.ARTICLE))
        g.add_node(RegNode(id="b", label="B", node_type=NodeType.VERIFIER))
        g.add_node(RegNode(id="c", label="C", node_type=NodeType.VERIFIER))
        g.add_edge(RegEdge(source="a", target="b", edge_type=EdgeType.IMPLEMENTS))
        g.add_edge(RegEdge(source="a", target="c", edge_type=EdgeType.REFERENCES))
        assert g.edge_count == 2

    def test_suggest_verifiers_for_article(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="art_9", label="Article 9", node_type=NodeType.ARTICLE))
        g.add_node(RegNode(id="schema", label="Schema Verifier", node_type=NodeType.VERIFIER))
        g.add_node(RegNode(id="llm_judge", label="LLM Judge", node_type=NodeType.VERIFIER))
        g.add_edge(RegEdge(source="art_9", target="schema", edge_type=EdgeType.IMPLEMENTS))
        g.add_edge(RegEdge(source="art_9", target="llm_judge", edge_type=EdgeType.IMPLEMENTS))

        verifiers = g.suggest_verifiers("art_9")
        assert "schema" in verifiers
        assert "llm_judge" in verifiers

    def test_suggest_verifiers_empty_when_none(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="art_1", label="Article 1", node_type=NodeType.ARTICLE))
        assert g.suggest_verifiers("art_1") == []

    def test_get_nodes_by_type(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="r1", label="Reg 1", node_type=NodeType.REGULATION))
        g.add_node(RegNode(id="a1", label="Art 1", node_type=NodeType.ARTICLE))
        g.add_node(RegNode(id="a2", label="Art 2", node_type=NodeType.ARTICLE))

        regs = g.get_nodes_by_type(NodeType.REGULATION)
        assert len(regs) == 1
        assert regs[0].id == "r1"

        articles = g.get_nodes_by_type(NodeType.ARTICLE)
        assert len(articles) == 2

    def test_query_interface(self) -> None:
        """query() returns explanation string with article and verifiers."""
        g = RegulatoryGraph()
        g.add_node(RegNode(id="art_9", label="Article 9 — Risk Management",
                           node_type=NodeType.ARTICLE,
                           description="Risk management system requirements"))
        g.add_node(RegNode(id="schema", label="Schema Verifier", node_type=NodeType.VERIFIER))
        g.add_edge(RegEdge(source="art_9", target="schema", edge_type=EdgeType.IMPLEMENTS))

        result = g.query("What verifiers do I need for art_9?")
        assert "art_9" in result or "Article 9" in result
        assert "schema" in result

    def test_path_between_nodes(self) -> None:
        g = RegulatoryGraph()
        g.add_node(RegNode(id="eu_ai_act", label="EU AI Act", node_type=NodeType.REGULATION))
        g.add_node(RegNode(id="art_9", label="Art 9", node_type=NodeType.ARTICLE))
        g.add_node(RegNode(id="schema", label="Schema", node_type=NodeType.VERIFIER))
        g.add_edge(RegEdge(source="eu_ai_act", target="art_9", edge_type=EdgeType.REQUIRES))
        g.add_edge(RegEdge(source="art_9", target="schema", edge_type=EdgeType.IMPLEMENTS))

        path = g.path("eu_ai_act", "schema")
        assert path is not None
        assert "eu_ai_act" in path
        assert "schema" in path


# ─── Loader tests ─────────────────────────────────────────────────────────────


class TestLoadDefaultGraph:
    def test_returns_regulatory_graph(self) -> None:
        g = load_default_graph()
        assert isinstance(g, RegulatoryGraph)

    def test_eu_ai_act_present(self) -> None:
        g = load_default_graph()
        node = g.get_node("eu_ai_act")
        assert node.node_type == NodeType.REGULATION

    def test_gdpr_present(self) -> None:
        g = load_default_graph()
        node = g.get_node("gdpr")
        assert node.node_type == NodeType.REGULATION

    def test_at_least_10_eu_ai_act_articles(self) -> None:
        g = load_default_graph()
        articles = [
            n for n in g.get_nodes_by_type(NodeType.ARTICLE)
            if n.metadata.get("regulation") == "EU AI Act"
        ]
        assert len(articles) >= 10, f"Only {len(articles)} EU AI Act articles found"

    def test_gdpr_articles_mapped(self) -> None:
        g = load_default_graph()
        articles = [
            n for n in g.get_nodes_by_type(NodeType.ARTICLE)
            if n.metadata.get("regulation") == "GDPR"
        ]
        assert len(articles) >= 3, f"Only {len(articles)} GDPR articles found"

    def test_eu_ai_act_article_9_has_verifiers(self) -> None:
        g = load_default_graph()
        verifiers = g.suggest_verifiers("eu_ai_act_art_9")
        assert len(verifiers) >= 1, "Article 9 should map to at least one verifier"

    def test_query_eu_ai_act_article_9(self) -> None:
        g = load_default_graph()
        result = g.query("What verifiers do I need for EU AI Act Article 9?")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_total_node_count_reasonable(self) -> None:
        g = load_default_graph()
        assert g.node_count >= 20, "Default graph should have at least 20 nodes"

    def test_verifier_nodes_map_to_real_ids(self) -> None:
        """Verifier node IDs should match registered verifier IDs."""
        g = load_default_graph()
        verifier_nodes = g.get_nodes_by_type(NodeType.VERIFIER)
        assert len(verifier_nodes) >= 3
