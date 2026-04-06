"""
veridian.integrations
─────────────────────
Cross-framework adapters — use Veridian verification with LangGraph,
CrewAI, AutoGen, and other agent frameworks without code rewrite.

GAP 2 FIX: "Teams won't rewrite their LangGraph/CrewAI agents.
Veridian needs adapter plugins." (Grok + KM research analysis)
"""

from veridian.integrations.universal import UniversalVerifier, VerificationGate

__all__ = ["UniversalVerifier", "VerificationGate"]
