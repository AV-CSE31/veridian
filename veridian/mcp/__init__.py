"""
veridian.mcp
────────────
MCP Skill Server and Federated Trust for cross-tool skill sharing.

Public API::

    from veridian.mcp import MCPSkillServer, FederatedTrustManager
"""

from veridian.mcp.server import MCPSkillServer, SkillRequest, SkillResponse, ToolDefinition
from veridian.mcp.trust import FederatedTrustManager, OrgTrustRecord, SkillProvenance, TrustDecision

__all__ = [
    "FederatedTrustManager",
    "MCPSkillServer",
    "OrgTrustRecord",
    "SkillProvenance",
    "SkillRequest",
    "SkillResponse",
    "ToolDefinition",
    "TrustDecision",
]
