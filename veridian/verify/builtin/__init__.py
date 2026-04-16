"""
veridian.verify.builtin
───────────────────────
Built-in verifiers imported here to self-register with the module-level
``registry`` singleton on ``import veridian``.

Registration order matters for CompositeVerifier / AnyOfVerifier:
simpler verifiers are registered first so they are available when
composite verifiers resolve sub-verifier IDs.
"""

from __future__ import annotations

from veridian.verify.base import registry

# Chain verifiers (depend on the above being registered)
from veridian.verify.builtin.any_of import AnyOfVerifier

# Core deterministic verifiers
from veridian.verify.builtin.bash import BashExitCodeVerifier
from veridian.verify.builtin.composite import CompositeVerifier
from veridian.verify.builtin.confidence import SelfConsistencyVerifier
from veridian.verify.builtin.file_exists import FileExistsVerifier
from veridian.verify.builtin.http import HttpStatusVerifier

# LLM-based (last - never standalone)
from veridian.verify.builtin.llm_judge import LLMJudgeVerifier

# Phase 10 - MCP skill server verifiers
from veridian.verify.builtin.mcp_tool_call import MCPToolCallVerifier
from veridian.verify.builtin.memory_integrity import MemoryIntegrityVerifier
from veridian.verify.builtin.prm_reference import PRMReferenceVerifier
from veridian.verify.builtin.quote import QuoteMatchVerifier
from veridian.verify.builtin.schema import SchemaVerifier

# Audit F3 - secrets leak detection (was orphaned per 08-code-cleanup audit)
from veridian.verify.builtin.secrets_guard import SecretsGuard
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

# Gap 5 fix - state verification
from veridian.verify.builtin.state_diff import StateDiffVerifier

# Phase 6b - safety verifiers
from veridian.verify.builtin.tool_safety import ToolSafetyVerifier

# Register all built-ins with the global registry
registry.register_many(
    BashExitCodeVerifier,
    QuoteMatchVerifier,
    SchemaVerifier,
    HttpStatusVerifier,
    FileExistsVerifier,
    SemanticGroundingVerifier,
    SelfConsistencyVerifier,
    CompositeVerifier,
    AnyOfVerifier,
    LLMJudgeVerifier,
    ToolSafetyVerifier,
    MemoryIntegrityVerifier,
    StateDiffVerifier,
    PRMReferenceVerifier,
    SecretsGuard,
    MCPToolCallVerifier,
)

__all__ = [
    "BashExitCodeVerifier",
    "QuoteMatchVerifier",
    "SchemaVerifier",
    "HttpStatusVerifier",
    "FileExistsVerifier",
    "SemanticGroundingVerifier",
    "SelfConsistencyVerifier",
    "CompositeVerifier",
    "AnyOfVerifier",
    "LLMJudgeVerifier",
    "ToolSafetyVerifier",
    "MemoryIntegrityVerifier",
    "StateDiffVerifier",
    "PRMReferenceVerifier",
    "SecretsGuard",
    "MCPToolCallVerifier",
]
