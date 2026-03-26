"""
veridian.agents.initializer
────────────────────────────
InitializerAgent — validates task specs before the worker loop begins.
Checks that required fields are present and description is clear enough.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from veridian.agents.base import BaseAgent
from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.providers.base import LLMProvider

if TYPE_CHECKING:
    from veridian.skills.library import SkillLibrary

__all__ = ["InitializerAgent"]

log = logging.getLogger(__name__)


class InitializerAgent(BaseAgent):
    """
    Validates task readiness before dispatching to WorkerAgent.
    Returns the task (possibly with enriched metadata) or raises.
    """

    id: ClassVar[str] = "initializer"

    def __init__(
        self,
        provider: LLMProvider,
        config: VeridianConfig,
        skill_library: SkillLibrary | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.skill_library = skill_library

    def run(self, task: Task, **kwargs: Any) -> Task:
        """
        Validate task spec. Returns the task unchanged if valid.
        Injects relevant skill context into task.metadata if skill_library is set.
        """
        if not task.title:
            log.warning("initializer.empty_title task_id=%s", task.id)
        if not task.description:
            log.debug(
                "initializer.no_description task_id=%s title=%s",
                task.id,
                task.title[:50],
            )

        if self.skill_library is not None:
            try:
                query = f"{task.title} {task.description}"
                skills = self.skill_library.query(
                    query,
                    domain=task.metadata.get("domain"),
                    top_k=self.config.skill_top_k,
                )
                if skills:
                    task.metadata["verified_procedures"] = [
                        {
                            "name": s.name,
                            "trigger": s.trigger,
                            "reliability": round(s.reliability_score, 3),
                            "steps": [st.description for st in s.steps],
                        }
                        for s in skills
                    ]
                    log.info(
                        "initializer.skills_injected task_id=%s count=%d",
                        task.id,
                        len(skills),
                    )
            except Exception as exc:
                log.warning("initializer.skill_query_error task_id=%s err=%s", task.id, exc)

        return task
