"""
veridian.knowledge.loader
─────────────────────────
Pre-loaded regulatory knowledge graph.

Regulations covered:
  - EU AI Act (12 key articles mapped to verifiers)
  - GDPR (5 key articles mapped to verifiers)
  - HIPAA (stub)
  - SOC 2 (stub)
  - NIST AI RMF (stub)

Call load_default_graph() to get a ready-to-use RegulatoryGraph.
"""

from __future__ import annotations

from veridian.knowledge.graph import RegulatoryGraph
from veridian.knowledge.models import EdgeType, NodeType, RegEdge, RegNode

# ─── Singleton cache ──────────────────────────────────────────────────────────
_cached_graph: RegulatoryGraph | None = None


def load_default_graph(*, force_reload: bool = False) -> RegulatoryGraph:
    """
    Return the pre-loaded regulatory knowledge graph.

    The graph is built once and cached in memory. Pass force_reload=True
    to rebuild from scratch (useful in tests).
    """
    global _cached_graph
    if _cached_graph is not None and not force_reload:
        return _cached_graph
    _cached_graph = _build_default_graph()
    return _cached_graph


def _build_default_graph() -> RegulatoryGraph:
    g = RegulatoryGraph()

    # ── Verifier nodes ────────────────────────────────────────────────────────
    _verifiers = [
        ("schema", "Schema Verifier", "Validates structured output against a JSON schema"),
        ("llm_judge", "LLM Judge Verifier", "Uses an LLM to evaluate output quality"),
        ("bash_exit", "Bash Exit Code Verifier", "Checks that a bash command exits with code 0"),
        ("quote_match", "Quote Match Verifier", "Verifies that quotes are grounded in source docs"),
        ("http_status", "HTTP Status Verifier", "Checks HTTP endpoint returns expected status"),
        ("file_exists", "File Exists Verifier", "Confirms required files are present"),
        ("composite", "Composite Verifier", "Chains multiple verifiers with AND logic"),
        ("tool_safety", "Tool Safety Verifier", "Static analysis of agent-generated code"),
        ("memory_integrity", "Memory Integrity Verifier", "Validates memory update consistency"),
        ("embedding_grounding", "Embedding Grounding Verifier", "Semantic similarity grounding"),
    ]
    for vid, label, desc in _verifiers:
        g.add_node(RegNode(id=vid, label=label, node_type=NodeType.VERIFIER, description=desc))

    # ── EU AI Act ─────────────────────────────────────────────────────────────
    g.add_node(
        RegNode(
            id="eu_ai_act",
            label="EU AI Act",
            node_type=NodeType.REGULATION,
            description="Regulation (EU) 2024/1689 on artificial intelligence",
            metadata={"jurisdiction": "EU", "year": 2024},
        )
    )

    eu_articles = [
        (
            "eu_ai_act_art_5",
            "Article 5",
            "Prohibited AI practices",
            "Prohibition of AI systems that manipulate, exploit, or cause harm",
            ["llm_judge", "tool_safety"],
        ),
        (
            "eu_ai_act_art_6",
            "Article 6",
            "Classification rules for high-risk AI",
            "Rules for classifying AI systems as high-risk",
            ["schema", "llm_judge"],
        ),
        (
            "eu_ai_act_art_9",
            "Article 9",
            "Risk management system",
            "Requirements for establishing a risk management system",
            ["schema", "composite", "llm_judge"],
        ),
        (
            "eu_ai_act_art_10",
            "Article 10",
            "Data and data governance",
            "Data quality requirements and governance for training, validation and testing",
            ["schema", "embedding_grounding"],
        ),
        (
            "eu_ai_act_art_11",
            "Article 11",
            "Technical documentation",
            "Technical documentation obligations for high-risk AI systems",
            ["file_exists", "schema"],
        ),
        (
            "eu_ai_act_art_12",
            "Article 12",
            "Record-keeping",
            "Automatic logging and record-keeping for high-risk AI systems",
            ["bash_exit", "file_exists"],
        ),
        (
            "eu_ai_act_art_13",
            "Article 13",
            "Transparency and provision of information",
            "Transparency obligations for high-risk AI systems",
            ["llm_judge", "schema"],
        ),
        (
            "eu_ai_act_art_14",
            "Article 14",
            "Human oversight",
            "Human oversight measures for high-risk AI systems",
            ["llm_judge", "tool_safety"],
        ),
        (
            "eu_ai_act_art_15",
            "Article 15",
            "Accuracy, robustness and cybersecurity",
            "Performance and security requirements for high-risk AI",
            ["schema", "composite", "tool_safety"],
        ),
        (
            "eu_ai_act_art_52",
            "Article 52",
            "Transparency obligations for certain AI systems",
            "Disclosure requirements when interacting with AI systems",
            ["llm_judge"],
        ),
        (
            "eu_ai_act_art_53",
            "Article 53",
            "General-purpose AI model obligations",
            "Requirements for GPAI models including capability evaluation",
            ["llm_judge", "schema", "embedding_grounding"],
        ),
        (
            "eu_ai_act_art_55",
            "Article 55",
            "Systemic risk obligations for GPAI",
            "Additional obligations for GPAI models posing systemic risk",
            ["composite", "llm_judge", "tool_safety"],
        ),
    ]

    for art_id, art_label, art_title, art_desc, verifier_ids in eu_articles:
        g.add_node(
            RegNode(
                id=art_id,
                label=f"{art_label} — {art_title}",
                node_type=NodeType.ARTICLE,
                description=art_desc,
                metadata={"regulation": "EU AI Act", "article_number": art_label},
            )
        )
        g.add_edge(RegEdge(source="eu_ai_act", target=art_id, edge_type=EdgeType.REQUIRES))
        for vid in verifier_ids:
            g.add_edge(RegEdge(source=art_id, target=vid, edge_type=EdgeType.IMPLEMENTS))

    # ── GDPR ──────────────────────────────────────────────────────────────────
    g.add_node(
        RegNode(
            id="gdpr",
            label="GDPR",
            node_type=NodeType.REGULATION,
            description="General Data Protection Regulation (EU) 2016/679",
            metadata={"jurisdiction": "EU", "year": 2018},
        )
    )

    gdpr_articles = [
        (
            "gdpr_art_5",
            "Article 5",
            "Principles relating to processing",
            "Lawfulness, fairness, transparency, purpose limitation, data minimisation",
            ["schema", "llm_judge"],
        ),
        (
            "gdpr_art_17",
            "Article 17",
            "Right to erasure",
            "Right to erasure ('right to be forgotten') — data deletion requirements",
            ["bash_exit", "file_exists"],
        ),
        (
            "gdpr_art_22",
            "Article 22",
            "Automated decision-making",
            "Right not to be subject to solely automated decisions with legal effects",
            ["llm_judge", "schema"],
        ),
        (
            "gdpr_art_25",
            "Article 25",
            "Data protection by design",
            "Data protection by design and by default requirements",
            ["tool_safety", "schema"],
        ),
        (
            "gdpr_art_35",
            "Article 35",
            "Data protection impact assessment",
            "DPIA requirements for high-risk processing activities",
            ["llm_judge", "composite"],
        ),
    ]

    for art_id, art_label, art_title, art_desc, verifier_ids in gdpr_articles:
        g.add_node(
            RegNode(
                id=art_id,
                label=f"{art_label} — {art_title}",
                node_type=NodeType.ARTICLE,
                description=art_desc,
                metadata={"regulation": "GDPR", "article_number": art_label},
            )
        )
        g.add_edge(RegEdge(source="gdpr", target=art_id, edge_type=EdgeType.REQUIRES))
        for vid in verifier_ids:
            g.add_edge(RegEdge(source=art_id, target=vid, edge_type=EdgeType.IMPLEMENTS))

    # Cross-regulation references
    g.add_edge(
        RegEdge(
            source="eu_ai_act_art_10",
            target="gdpr_art_5",
            edge_type=EdgeType.REFERENCES,
            metadata={"note": "EU AI Act data governance references GDPR data principles"},
        )
    )
    g.add_edge(
        RegEdge(
            source="eu_ai_act_art_13",
            target="gdpr_art_22",
            edge_type=EdgeType.REFERENCES,
            metadata={"note": "Transparency overlaps with GDPR automated decision rights"},
        )
    )

    # ── HIPAA (stub) ──────────────────────────────────────────────────────────
    g.add_node(
        RegNode(
            id="hipaa",
            label="HIPAA",
            node_type=NodeType.REGULATION,
            description="Health Insurance Portability and Accountability Act (US)",
            metadata={"jurisdiction": "US", "year": 1996},
        )
    )
    g.add_node(
        RegNode(
            id="hipaa_privacy_rule",
            label="HIPAA Privacy Rule",
            node_type=NodeType.ARTICLE,
            description="Standards for the protection of PHI",
            metadata={"regulation": "HIPAA"},
        )
    )
    g.add_edge(RegEdge(source="hipaa", target="hipaa_privacy_rule", edge_type=EdgeType.REQUIRES))
    g.add_edge(RegEdge(source="hipaa_privacy_rule", target="schema", edge_type=EdgeType.IMPLEMENTS))

    # ── SOC 2 (stub) ──────────────────────────────────────────────────────────
    g.add_node(
        RegNode(
            id="soc2",
            label="SOC 2",
            node_type=NodeType.REGULATION,
            description="Service Organization Controls 2 — security and availability",
            metadata={"jurisdiction": "US"},
        )
    )
    g.add_node(
        RegNode(
            id="soc2_cc6",
            label="SOC 2 CC6 — Logical and Physical Access",
            node_type=NodeType.ARTICLE,
            description="Logical and physical access controls",
            metadata={"regulation": "SOC2"},
        )
    )
    g.add_edge(RegEdge(source="soc2", target="soc2_cc6", edge_type=EdgeType.REQUIRES))
    g.add_edge(RegEdge(source="soc2_cc6", target="tool_safety", edge_type=EdgeType.IMPLEMENTS))

    # ── NIST AI RMF (stub) ────────────────────────────────────────────────────
    g.add_node(
        RegNode(
            id="nist_ai_rmf",
            label="NIST AI RMF",
            node_type=NodeType.REGULATION,
            description="NIST Artificial Intelligence Risk Management Framework 1.0",
            metadata={"jurisdiction": "US", "year": 2023},
        )
    )
    g.add_node(
        RegNode(
            id="nist_ai_rmf_govern",
            label="NIST AI RMF — GOVERN",
            node_type=NodeType.ARTICLE,
            description="Establish policies, processes, and organizational roles for AI risk",
            metadata={"regulation": "NIST AI RMF"},
        )
    )
    g.add_node(
        RegNode(
            id="nist_ai_rmf_map",
            label="NIST AI RMF — MAP",
            node_type=NodeType.ARTICLE,
            description="Identify and categorize AI risks in context",
            metadata={"regulation": "NIST AI RMF"},
        )
    )
    g.add_edge(
        RegEdge(source="nist_ai_rmf", target="nist_ai_rmf_govern", edge_type=EdgeType.REQUIRES)
    )
    g.add_edge(RegEdge(source="nist_ai_rmf", target="nist_ai_rmf_map", edge_type=EdgeType.REQUIRES))
    g.add_edge(
        RegEdge(source="nist_ai_rmf_govern", target="llm_judge", edge_type=EdgeType.IMPLEMENTS)
    )
    g.add_edge(RegEdge(source="nist_ai_rmf_map", target="schema", edge_type=EdgeType.IMPLEMENTS))

    return g


__all__ = ["load_default_graph"]
