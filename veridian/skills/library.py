"""
veridian.skills.library
────────────────────────
SkillLibrary — facade for verified procedural memory.

After every successful run: extracts reusable procedures from DONE tasks.
Before every run: query() surfaces relevant prior skills for the InitializerAgent.

INVARIANT: Skills are ONLY extracted from TaskStatus.DONE tasks that passed
verification. Unverified completions are never stored.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from veridian.loop.runtime_store import RuntimeStore
from veridian.providers.base import LLMProvider
from veridian.skills.admission import SkillAdmissionControl
from veridian.skills.extractor import SkillExtractor
from veridian.skills.models import Skill, SkillCandidate, SkillStep
from veridian.skills.store import SkillStore

__all__ = ["SkillLibrary"]

log = logging.getLogger(__name__)


class SkillLibrary:
    """
    High-level facade for skill extraction, storage, and retrieval.

    Opt-in: VeridianRunner works identically with skill_library=None.
    All methods are safe to call even with an empty store.
    """

    def __init__(
        self,
        store_path: str | Path = "skills.json",
        provider: LLMProvider | None = None,
        min_confidence: float = 0.70,
        max_retries_for_skill: int = 1,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._store = SkillStore(path=store_path, embed_fn=embed_fn)
        self._extractor = SkillExtractor(
            provider=provider,
            max_retries_for_skill=max_retries_for_skill,
        )
        self._admission = SkillAdmissionControl(
            store=self._store,
            min_confidence=min_confidence,
            max_retries_for_skill=max_retries_for_skill,
            embed_fn=embed_fn or self._store.embed_fn,
        )

    # ── Core operations ───────────────────────────────────────────────────────

    def post_run(self, ledger: RuntimeStore, run_id: str) -> list[str]:
        """
        Extract skills from completed tasks in ledger.
        Returns list of skill IDs for newly admitted skills.
        Called after VeridianRunner.run() completes.
        """
        candidates = self._extractor.extract(ledger, run_id)
        admitted_ids: list[str] = []
        for candidate in candidates:
            ok, reason = self._admission.admit(candidate)
            if not ok:
                log.debug(
                    "skill_library.rejected task_id=%s reason=%s",
                    candidate.task_id,
                    reason,
                )
                continue
            skill = self._candidate_to_skill(candidate)
            skill.embedding = self._store.embed_fn(skill.trigger)
            self._store.save(skill)
            admitted_ids.append(skill.id)
            log.info(
                "skill_library.admitted skill_id=%s name=%r task_id=%s",
                skill.id,
                skill.name,
                candidate.task_id,
            )
        return admitted_ids

    def query(
        self,
        task_description: str,
        domain: str | None = None,
        top_k: int = 3,
    ) -> list[Skill]:
        """
        Retrieve top-k relevant skills for a task description.
        Results are ranked by bayesian_lower_bound (MACLA).
        """
        results = self._store.query(task_description, domain=domain, top_k=top_k)
        return [skill for skill, _sim in results]

    def record_outcome(self, skill_id: str, success: bool) -> None:
        """
        Update Bayesian reliability for a skill after it was used.
        MUST be called after every skill-guided task.
        """
        self._store.update_reliability(skill_id, success=success)

    def stats(self) -> dict[str, Any]:
        """Return summary statistics for the skill library."""
        return self._store.stats()

    # ── Import / export ───────────────────────────────────────────────────────

    def export(self, path: str | Path) -> None:
        """
        Atomically export all skills to a JSON file.
        Uses temp-file + os.replace() — partial writes never occur.
        """
        export_path = Path(path)
        skills = self._store.list()
        payload = {
            "schema_version": 1,
            "skills": [s.to_dict() for s in skills],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        json.loads(text)  # validate round-trip

        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=export_path.parent, suffix=".tmp", prefix="skills_export_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path_str, export_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path_str)
            raise
        log.info("skill_library.export path=%s count=%d", export_path, len(skills))

    def import_skills(self, path: str | Path, overwrite: bool = False) -> int:
        """
        Import skills from an exported JSON file.
        Returns count of skills actually imported.
        """
        import_path = Path(path)
        text = import_path.read_text(encoding="utf-8")
        payload = json.loads(text)
        skills_raw: list[dict[str, Any]] = payload.get("skills", [])

        imported = 0
        for raw in skills_raw:
            skill = Skill.from_dict(raw)
            existing = self._store.get(skill.id)
            if existing is not None and not overwrite:
                continue
            if not skill.embedding:
                skill.embedding = self._store.embed_fn(skill.trigger)
            self._store.save(skill)
            imported += 1

        log.info("skill_library.import path=%s imported=%d", import_path, imported)
        return imported

    # ── Internal ──────────────────────────────────────────────────────────────

    def _candidate_to_skill(self, candidate: SkillCandidate) -> Skill:
        """Convert an admitted SkillCandidate to a Skill object."""
        steps: list[SkillStep] = []
        for bo in candidate.bash_outputs:
            cmd = bo.get("cmd", "")
            steps.append(
                SkillStep(
                    description=f"Run: {cmd}" if cmd else "Execute command",
                    command=cmd or None,
                    exit_code_expected=bo.get("exit_code", 0),
                )
            )
        if not steps:
            steps = [SkillStep(description="Complete the task as described")]

        # Extract tool names from bash commands (first token of each cmd)
        tools_used: list[str] = []
        seen_tools: set[str] = set()
        for bo in candidate.bash_outputs:
            cmd = bo.get("cmd", "").strip()
            if cmd:
                tool = cmd.split()[0]
                if tool not in seen_tools:
                    tools_used.append(tool)
                    seen_tools.add(tool)

        trigger = (f"{candidate.task_title}: {candidate.task_description[:120]}").strip(": ")

        return Skill(
            id=str(uuid.uuid4()),
            name=candidate.task_title,
            trigger=trigger,
            domain=candidate.domain_hint or "generic",
            verifier_id=candidate.verifier_id,
            steps=steps,
            tools_used=tools_used,
            context_requirements=[],
            confidence_at_extraction=candidate.confidence,
            source_task_id=candidate.task_id,
            source_run_id=candidate.run_id,
        )
