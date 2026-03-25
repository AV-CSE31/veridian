"""
veridian.skills.extractor
─────────────────────────
SkillExtractor — scans a TaskLedger for DONE tasks and builds SkillCandidates.

INVARIANT: NEVER extracts from a task that is not TaskStatus.DONE.
Confidence is computed from retry_count (lower retries → higher confidence).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from veridian.core.task import Task, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.providers.base import LLMProvider
from veridian.skills.models import SkillCandidate

__all__ = ["SkillExtractor"]

log = logging.getLogger(__name__)

# Domain keyword map for _infer_domain
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "legal": ["legal", "contract", "clause", "law", "compliance", "agreement", "counsel"],
    "compliance": ["aml", "kyc", "fraud", "sanction", "regulatory", "audit"],
    "code-migration": ["migrate", "migration", "refactor", "upgrade", "port", "codebase"],
}


class SkillExtractor:
    """
    Builds SkillCandidates from qualifying DONE tasks in a TaskLedger.

    Filters applied (all must pass):
      1. task.status == DONE
      2. task.verifier_id not in SKIP_VERIFIERS
      3. task.retry_count <= max_retries_for_skill
      4. computed confidence >= MIN_CONFIDENCE
    """

    MIN_CONFIDENCE: ClassVar[float] = 0.70
    SKIP_VERIFIERS: ClassVar[set[str]] = {"self_consistency", "llm_judge"}

    def __init__(
        self,
        provider: LLMProvider | None = None,
        max_retries_for_skill: int = 1,
    ) -> None:
        self.provider = provider
        self.max_retries_for_skill = max_retries_for_skill

    def extract(self, ledger: TaskLedger, run_id: str) -> list[SkillCandidate]:
        """
        Scan all tasks in ledger and return SkillCandidates for qualifying DONE tasks.
        run_id is stored in each candidate for provenance.
        """
        tasks = ledger.list()
        candidates: list[SkillCandidate] = []
        for task in tasks:
            candidate = self._extract_one(task, run_id)
            if candidate is not None:
                candidates.append(candidate)
        log.debug(
            "skill_extractor.extract run_id=%s tasks=%d candidates=%d",
            run_id,
            len(tasks),
            len(candidates),
        )
        return candidates

    def _extract_one(self, task: Task, run_id: str) -> SkillCandidate | None:
        """Build a SkillCandidate from a single task, or return None if it fails filters."""
        # Filter 1: must be DONE
        if task.status != TaskStatus.DONE:
            return None

        # Filter 2: skip unreliable verifiers
        if task.verifier_id in self.SKIP_VERIFIERS:
            log.debug("skill_extractor.skip_verifier task_id=%s", task.id)
            return None

        # Filter 3: too many retries → unreliable procedure
        if task.retry_count > self.max_retries_for_skill:
            log.debug(
                "skill_extractor.skip_retries task_id=%s retry_count=%d max=%d",
                task.id,
                task.retry_count,
                self.max_retries_for_skill,
            )
            return None

        # Filter 4: confidence derived from retry_count
        confidence = max(0.0, 1.0 - task.retry_count * 0.25)
        if confidence < self.MIN_CONFIDENCE:
            log.debug(
                "skill_extractor.skip_confidence task_id=%s confidence=%.2f",
                task.id,
                confidence,
            )
            return None

        bash_outputs: list[dict[str, Any]] = []
        structured_output: dict[str, Any] = {}
        if task.result is not None:
            bash_outputs = task.result.bash_outputs
            structured_output = task.result.structured

        return SkillCandidate(
            task_id=task.id,
            run_id=run_id,
            task_title=task.title,
            task_description=task.description,
            verifier_id=task.verifier_id,
            confidence=confidence,
            retry_count=task.retry_count,
            bash_outputs=bash_outputs,
            structured_output=structured_output,
            domain_hint=self._infer_domain(task),
        )

    def _infer_domain(self, task: Task) -> str:
        """Guess domain from task title and description keywords."""
        text = (task.title + " " + task.description).lower()
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return domain
        return "generic"
