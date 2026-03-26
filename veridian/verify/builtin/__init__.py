"""
veridian.verify.builtin
───────────────────────
All 10 built-in verifiers. Imported here to self-register with the module-level
``registry`` singleton on ``import veridian``.

Registration order matters for CompositeVerifier / AnyOfVerifier:
simpler verifiers are registered first so they are available when
composite verifiers resolve sub-verifier IDs.
"""

from __future__ import annotations

from veridian.verify.base import registry

# ── Chain verifiers (depend on the above being registered) ────────────────────
from veridian.verify.builtin.any_of import AnyOfVerifier

# ── Core deterministic verifiers (register first) ────────────────────────────
from veridian.verify.builtin.bash import BashExitCodeVerifier
from veridian.verify.builtin.composite import CompositeVerifier
from veridian.verify.builtin.confidence import SelfConsistencyVerifier
from veridian.verify.builtin.file_exists import FileExistsVerifier
from veridian.verify.builtin.http import HttpStatusVerifier

# ── LLM-based (last — never standalone) ──────────────────────────────────────
from veridian.verify.builtin.llm_judge import LLMJudgeVerifier
from veridian.verify.builtin.quote import QuoteMatchVerifier
from veridian.verify.builtin.schema import SchemaVerifier
from veridian.verify.builtin.semantic_grounding import SemanticGroundingVerifier

# Register all 10 with the global registry
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
]
