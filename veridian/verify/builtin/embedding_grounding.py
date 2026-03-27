"""
veridian.verify.builtin.embedding_grounding
────────────────────────────────────────────
A6: Embedding-based semantic grounding verifier.

Checks whether an agent's output is factually grounded in a provided context
by computing cosine similarity between TF-IDF vectors of the context and output.

This verifier is DISTINCT from ``SemanticGroundingVerifier`` (which does rule-based
cross-field consistency checks).  This verifier uses vector similarity to catch
outputs that are semantically unrelated to the provided reference context.

Design rules:
- Never calls an LLM (uses TF-IDF cosine similarity — pure stdlib + math)
- Stateless and idempotent
- Optionally uses ``sentence-transformers`` if installed (higher quality embeddings)
- Context can be provided at construction time OR via ``task.verifier_config["context"]``
- Empty context → auto-pass (no grounding possible without reference material)

Usage::

    from veridian.verify.builtin.embedding_grounding import EmbeddingGroundingVerifier

    verifier = EmbeddingGroundingVerifier(
        context="The quarterly revenue increased by 12% driven by cloud services.",
        threshold=0.35,
    )
    result = verifier.verify(task, task_result)
    # result.passed is True if cosine_similarity(context, output) >= threshold
"""

from __future__ import annotations

import math
import re
from collections import Counter

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

# ── TF-IDF utilities (stdlib only) ───────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokenizer — no external dependencies."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _tfidf_vector(tokens: list[str]) -> dict[str, float]:
    """Return a simple TF vector (term-frequency, no IDF needed for two-doc cosine)."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {term: count / total for term, count in counts.items()}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two TF vectors."""
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in shared)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _compute_similarity(context: str, output: str) -> float:
    """
    Compute semantic similarity between context and output.

    Tries ``sentence-transformers`` first (higher quality); falls back to
    TF-IDF cosine similarity (stdlib only).
    """
    try:
        from sentence_transformers import SentenceTransformer, util

        _model = SentenceTransformer("all-MiniLM-L6-v2")
        emb_ctx = _model.encode(context, convert_to_tensor=True)
        emb_out = _model.encode(output, convert_to_tensor=True)
        score: float = float(util.cos_sim(emb_ctx, emb_out).item())
        return max(0.0, min(1.0, score))
    except ImportError:
        pass
    except Exception:
        pass

    # stdlib TF-IDF fallback
    vec_ctx = _tfidf_vector(_tokenize(context))
    vec_out = _tfidf_vector(_tokenize(output))
    return _cosine_similarity(vec_ctx, vec_out)


# ── EmbeddingGroundingVerifier ────────────────────────────────────────────────


class EmbeddingGroundingVerifier(BaseVerifier):
    """
    Embedding-based semantic grounding verifier.

    Computes the cosine similarity between the reference *context* and the
    agent's output.  Fails if the similarity is below *threshold*.

    - No LLM calls — uses TF-IDF (stdlib) or ``sentence-transformers`` if installed.
    - Stateless: safe for concurrent use.
    - Context priority: constructor ``context`` → ``task.verifier_config["context"]``.
    - Empty context → auto-pass (nothing to ground against).

    Parameters
    ----------
    context:
        Reference text the output should be grounded in.
    threshold:
        Minimum cosine similarity score to pass (0.0-1.0, default 0.25).
    """

    id = "embedding_grounding"
    description = (
        "Checks whether the agent output is semantically grounded in the provided context "
        "using cosine similarity (TF-IDF fallback or sentence-transformers)."
    )

    def __init__(
        self,
        context: str = "",
        threshold: float = 0.25,
    ) -> None:
        """Initialize with optional reference context and similarity threshold."""
        self._context = context
        self._threshold = threshold

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Return passed=True if output cosine similarity >= threshold."""
        # Resolve context: constructor → verifier_config → empty
        context = self._context or task.verifier_config.get("context", "")
        threshold = float(task.verifier_config.get("threshold", self._threshold))

        # Empty context → auto-pass
        if not context.strip():
            return VerificationResult(
                passed=True,
                evidence={"note": "no context provided — grounding check skipped"},
            )

        output = result.raw_output.strip()

        # Empty output with context → treat as similarity=0
        similarity = 0.0 if not output else _compute_similarity(context, output)

        passed = similarity >= threshold

        if passed:
            return VerificationResult(
                passed=True,
                evidence={"similarity": round(similarity, 4), "threshold": threshold},
            )

        return VerificationResult(
            passed=False,
            evidence={"similarity": round(similarity, 4), "threshold": threshold},
            error=(
                f"[embedding grounding] Output similarity {similarity:.3f} is below "
                f"threshold {threshold:.3f}. Ensure the output is grounded in the "
                f"provided context."
            )[:300],
        )
