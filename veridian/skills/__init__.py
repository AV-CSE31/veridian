"""
veridian.skills
───────────────
SkillLibrary — verified procedural memory for Veridian agents.

Skills are extracted ONLY from TaskStatus.DONE tasks that passed verification.
Retrieval is embedding-based (Voyager) with Bayesian reliability ranking (MACLA).

Quick start::

    from veridian.skills import SkillLibrary

    lib = SkillLibrary(store_path="skills.json")

    # After a run completes:
    skill_ids = lib.post_run(ledger, run_id="run_001")

    # Before the next run — query for relevant prior procedures:
    skills = lib.query("Extract change-of-control clause from PDF", top_k=3)
    for skill in skills:
        print(f"[{skill.reliability_score:.0%}] {skill.name}")
        for step in skill.steps:
            print(f"  → {step.description}")
"""

from veridian.skills.admission import SkillAdmissionControl
from veridian.skills.blast_radius import BlastRadiusAnalyzer, ImpactReport
from veridian.skills.extractor import SkillExtractor
from veridian.skills.library import SkillLibrary
from veridian.skills.models import Skill, SkillCandidate, SkillStep
from veridian.skills.quarantine import QuarantineResult, QuarantineStatus, SkillQuarantine
from veridian.skills.store import SkillStore

__all__ = [
    "BlastRadiusAnalyzer",
    "ImpactReport",
    "QuarantineResult",
    "QuarantineStatus",
    "Skill",
    "SkillAdmissionControl",
    "SkillCandidate",
    "SkillExtractor",
    "SkillLibrary",
    "SkillQuarantine",
    "SkillStep",
    "SkillStore",
]
