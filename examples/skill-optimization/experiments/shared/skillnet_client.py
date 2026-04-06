"""
SkillNetClient — local-fallback wrapper.

In a real deployment this would call the SkillNet API to retrieve skills
and query them. Since SkillNet is not available in this environment, all
operations use the locally generated fixture data.

The interface is designed to match the SkillNet v2 API surface so
experiments can be run against real SkillNet by swapping the backend.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from examples.experiments.shared.config import DATA_DIR, RANDOM_SEED

log = logging.getLogger(__name__)

# ─── Fixture paths ────────────────────────────────────────────────────────────
_SKILLS_PATH = DATA_DIR / "skills.json"
_QUERIES_PATH = DATA_DIR / "queries.json"


class SkillNetClient:
    """
    Thin wrapper around local fixture data.

    Methods mirror the SkillNet v2 REST API:
      - list_skills(domain, limit)    → list[dict]
      - get_skill(skill_id)           → dict
      - query(text, limit, domain)    → list[dict]   (semantic search)
      - get_queries(distribution)     → list[dict]   ("in_dist" | "ood" | None)
    """

    def __init__(
        self,
        skills_path: Path | None = None,
        queries_path: Path | None = None,
        seed: int = RANDOM_SEED,
    ) -> None:
        self._skills_path = skills_path or _SKILLS_PATH
        self._queries_path = queries_path or _QUERIES_PATH
        self._rng = random.Random(seed)
        self._skills: list[dict] = []
        self._queries: list[dict] = []
        self._loaded = False

    # ── Lazy load ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._skills_path.exists():
            raise FileNotFoundError(
                f"Skill fixtures not found at {self._skills_path}. "
                "Run: python examples/fixtures/generate_fixtures.py"
            )
        with open(self._skills_path, encoding="utf-8") as f:
            self._skills = json.load(f)
        with open(self._queries_path, encoding="utf-8") as f:
            self._queries = json.load(f)
        self._loaded = True
        log.debug(
            "skillnet_client: loaded %d skills, %d queries",
            len(self._skills),
            len(self._queries),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def list_skills(
        self,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return skills, optionally filtered by domain."""
        self._ensure_loaded()
        skills = self._skills
        if domain:
            skills = [s for s in skills if s.get("domain") == domain]
        return skills[:limit]

    def get_skill(self, skill_id: str) -> dict:
        """Return a single skill by ID. Raises KeyError if not found."""
        self._ensure_loaded()
        for skill in self._skills:
            if skill["id"] == skill_id:
                return skill
        raise KeyError(f"Skill '{skill_id}' not found")

    def query(
        self,
        text: str,
        limit: int = 10,
        domain: str | None = None,
    ) -> list[dict]:
        """Semantic search stub — returns a random subset of domain skills."""
        self._ensure_loaded()
        pool = self._skills
        if domain:
            pool = [s for s in pool if s.get("domain") == domain]
        sample_size = min(limit, len(pool))
        return self._rng.sample(pool, sample_size)

    def get_queries(
        self,
        distribution: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return queries filtered by distribution ('in_dist' | 'ood' | None)."""
        self._ensure_loaded()
        queries = self._queries
        if distribution:
            queries = [q for q in queries if q.get("distribution") == distribution]
        if limit:
            queries = queries[:limit]
        return queries

    def domains(self) -> list[str]:
        """Return distinct domain names."""
        self._ensure_loaded()
        seen: list[str] = []
        for s in self._skills:
            d = s.get("domain", "unknown")
            if d not in seen:
                seen.append(d)
        return seen

    def total_skills(self) -> int:
        self._ensure_loaded()
        return len(self._skills)

    def total_queries(self) -> int:
        self._ensure_loaded()
        return len(self._queries)
