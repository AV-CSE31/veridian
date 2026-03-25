"""
veridian.skills.store
─────────────────────
SkillStore — JSON-backed skill persistence with embedding-based retrieval.

INVARIANTS:
  - All writes use atomic temp-file + os.replace() — no partial writes.
  - Embeddings are cached on first save; recomputed lazily if missing on query.
  - query() ranks by bayesian_lower_bound (MACLA), not raw cosine similarity.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from veridian.core.exceptions import VeridianConfigError
from veridian.skills.models import Skill

__all__ = ["SkillStore"]

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors. Returns 0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return float(dot / (mag_a * mag_b))


class SkillStore:
    """
    Persistent skill store backed by a JSON file.

    embed_fn: callable(text) → list[float].
    Defaults to hash-based mock. For production: sentence-transformers.
    """

    def __init__(
        self,
        path: str | Path = "skills.json",
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._path = Path(path)
        self.embed_fn: Callable[[str], list[float]] = embed_fn or self._default_embed
        if not self._path.exists():
            self._flush_data({"schema_version": _SCHEMA_VERSION, "skills": {}})

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self, skill: Skill) -> str:
        """Persist skill. Sets embedding if not already populated. Returns skill.id."""
        if not skill.embedding:
            skill.embedding = self.embed_fn(skill.trigger)
        data = self._load()
        data["skills"][skill.id] = skill.to_dict()
        self._flush_data(data)
        log.debug("skill_store.save id=%s name=%r", skill.id, skill.name)
        return skill.id

    def get(self, skill_id: str) -> Skill | None:
        """Return skill by ID, or None if not found."""
        raw = self._load()["skills"].get(skill_id)
        return Skill.from_dict(raw) if raw is not None else None

    def delete(self, skill_id: str) -> bool:
        """Remove skill by ID. Returns True if deleted, False if not found."""
        data = self._load()
        if skill_id not in data["skills"]:
            return False
        del data["skills"][skill_id]
        self._flush_data(data)
        return True

    def list(self, domain: str | None = None) -> builtins.list[Skill]:
        """Return all skills, optionally filtered by domain."""
        skills = [Skill.from_dict(v) for v in self._load()["skills"].values()]
        if domain is not None:
            skills = [s for s in skills if s.domain == domain]
        return skills

    def query(
        self,
        query: str,
        domain: str | None = None,
        top_k: int = 5,
        min_reliability: float = 0.4,
    ) -> builtins.list[tuple[Skill, float]]:
        """
        Retrieve top-k skills ranked by bayesian_lower_bound (MACLA).
        The float in each tuple is the cosine similarity score.
        min_reliability filters out low-confidence skills.
        """
        skills = self.list(domain=domain)
        if not skills:
            return []

        query_vec = self.embed_fn(query)
        scored: list[tuple[Skill, float]] = []

        for skill in skills:
            if skill.reliability_score < min_reliability:
                continue
            emb = skill.embedding if skill.embedding else self.embed_fn(skill.trigger)
            sim = _cosine_similarity(query_vec, emb)
            scored.append((skill, sim))

        # Primary sort: bayesian_lower_bound DESC (reliable skills first)
        # Secondary: cosine similarity DESC (tie-breaker)
        scored.sort(key=lambda t: (t[0].bayesian_lower_bound, t[1]), reverse=True)
        return scored[:top_k]

    def update_reliability(self, skill_id: str, success: bool) -> None:
        """Update alpha/beta_ for a skill and persist. No-op if ID unknown."""
        data = self._load()
        if skill_id not in data["skills"]:
            log.warning("skill_store.update_reliability unknown id=%s", skill_id)
            return
        skill = Skill.from_dict(data["skills"][skill_id])
        if success:
            skill.record_success()
        else:
            skill.record_failure()
        data["skills"][skill_id] = skill.to_dict()
        self._flush_data(data)

    def stats(self) -> dict[str, Any]:
        """Return summary statistics for the store."""
        skills = self.list()
        if not skills:
            return {"total_skills": 0, "avg_reliability": 0.0, "by_domain": {}}
        reliabilities = [s.reliability_score for s in skills]
        avg_r = sum(reliabilities) / len(reliabilities)
        domains: dict[str, int] = {}
        for s in skills:
            domains[s.domain] = domains.get(s.domain, 0) + 1
        return {
            "total_skills": len(skills),
            "avg_reliability": round(avg_r, 4),
            "by_domain": domains,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """Read and parse skills.json. Returns empty store on FileNotFoundError."""
        if not self._path.exists():
            return {"schema_version": _SCHEMA_VERSION, "skills": {}}
        try:
            text = self._path.read_text(encoding="utf-8")
            return dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise VeridianConfigError(f"skills.json is malformed: {exc}") from exc

    def _flush_data(self, data: dict[str, Any]) -> None:
        """Atomic write: serialize to temp file, then os.replace() to final path."""
        data["schema_version"] = _SCHEMA_VERSION
        text = json.dumps(data, indent=2, ensure_ascii=False)
        # Validate JSON round-trip before writing
        json.loads(text)

        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp", prefix="skills_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @staticmethod
    def _default_embed(text: str) -> builtins.list[float]:
        """
        Hash-based fallback embedding for tests and offline use.
        Production: use sentence-transformers/all-MiniLM-L6-v2 (lazy import).
        """
        try:
            from sentence_transformers import (
                SentenceTransformer,  # noqa: PLC0415
            )

            _model = SentenceTransformer("all-MiniLM-L6-v2")
            result: list[float] = _model.encode(text).tolist()
            return result
        except ImportError:
            pass
        h = hashlib.sha256(text.encode()).digest()
        return [(b / 127.5) - 1.0 for b in h]
