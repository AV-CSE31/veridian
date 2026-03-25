"""
veridian.skills.admission
─────────────────────────
SkillAdmissionControl — validates SkillCandidates before they enter the store.

Checks (in order):
  1. confidence >= min_confidence
  2. retry_count <= max_retries_for_skill
  3. len(bash_outputs) >= min_steps
  4. not a duplicate of an existing skill (cosine similarity check)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from veridian.skills.models import SkillCandidate
from veridian.skills.store import SkillStore, _cosine_similarity

__all__ = ["SkillAdmissionControl"]

log = logging.getLogger(__name__)


class SkillAdmissionControl:
    """
    Gate between SkillExtractor and SkillStore.
    All four checks must pass for a candidate to be admitted.
    """

    def __init__(
        self,
        store: SkillStore,
        min_confidence: float = 0.70,
        max_retries_for_skill: int = 1,
        min_steps: int = 2,
        dedup_threshold: float = 0.92,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._store = store
        self._min_confidence = min_confidence
        self._max_retries_for_skill = max_retries_for_skill
        self._min_steps = min_steps
        self._dedup_threshold = dedup_threshold
        self._embed_fn: Callable[[str], list[float]] = embed_fn or store.embed_fn

    def admit(self, candidate: SkillCandidate) -> tuple[bool, str]:
        """
        Evaluate a candidate. Returns (True, "admitted") or (False, rejection_reason).
        """
        # Check 1: confidence
        if candidate.confidence < self._min_confidence:
            reason = (
                f"confidence {candidate.confidence:.3f} below threshold {self._min_confidence:.3f}"
            )
            log.debug("admission.reject_confidence task_id=%s", candidate.task_id)
            return False, reason

        # Check 2: retry count
        if candidate.retry_count > self._max_retries_for_skill:
            reason = (
                f"retry_count {candidate.retry_count} exceeds max "
                f"{self._max_retries_for_skill} for skill extraction"
            )
            log.debug("admission.reject_retries task_id=%s", candidate.task_id)
            return False, reason

        # Check 3: minimum steps (bash_outputs as proxy)
        if len(candidate.bash_outputs) < self._min_steps:
            reason = (
                f"insufficient steps: {len(candidate.bash_outputs)} bash output(s), "
                f"need at least {self._min_steps}"
            )
            log.debug("admission.reject_steps task_id=%s", candidate.task_id)
            return False, reason

        # Check 4: deduplication
        if self._is_duplicate(candidate):
            reason = "duplicate skill detected (cosine similarity above dedup threshold)"
            log.debug("admission.reject_duplicate task_id=%s", candidate.task_id)
            return False, reason

        return True, "admitted"

    def _is_duplicate(self, candidate: SkillCandidate) -> bool:
        """
        Return True if any stored skill has cosine similarity >= dedup_threshold
        with this candidate's trigger embedding.
        """
        trigger_text = f"{candidate.task_title} {candidate.task_description}"
        candidate_embed = self._embed_fn(trigger_text)

        for skill in self._store.list():
            skill_embed = skill.embedding if skill.embedding else self._embed_fn(skill.trigger)
            sim = _cosine_similarity(candidate_embed, skill_embed)
            if sim >= self._dedup_threshold:
                log.debug(
                    "admission.duplicate_found skill_id=%s sim=%.3f threshold=%.3f",
                    skill.id,
                    sim,
                    self._dedup_threshold,
                )
                return True
        return False
