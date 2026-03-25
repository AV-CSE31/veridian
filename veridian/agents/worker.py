"""
veridian.agents.worker
───────────────────────
WorkerAgent — drives the agent-task interaction loop.

Loop contract (CLAUDE.md §6):
  - Iterate until: result found OR len(messages) > config.max_turns_per_task
  - No result + no bash commands → append nudge message
  - Result regex: <veridian:result>\\s*(\\{.*?\\})\\s*</veridian:result>  (DOTALL)
  - Never hardcode max_turns — always read from VeridianConfig
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from veridian.agents.base import BaseAgent
from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult
from veridian.providers.base import LLMProvider, Message

__all__ = ["WorkerAgent", "_RESULT_RE"]

log = logging.getLogger(__name__)

# Frozen contract — do NOT modify this regex (CLAUDE.md §6)
_RESULT_RE = re.compile(
    r"<veridian:result>\s*(\{.*?\})\s*</veridian:result>",
    re.DOTALL,
)

_BASH_RE = re.compile(r"```bash|<bash>|\$ ")

_NUDGE_MESSAGE = "Output a <veridian:result> block now."


class WorkerAgent(BaseAgent):
    """
    Drives the worker-agent interaction loop for a single task.

    The loop:
      1. Build context (injected ContextManager) or fallback to simple prompt
      2. Call provider.complete()
      3. If result found → return TaskResult
      4. If bash commands found → continue loop (executor handles separately)
      5. If neither → append nudge and continue
      6. Exit when max_turns_per_task exceeded
    """

    id: ClassVar[str] = "worker"

    def __init__(
        self,
        provider: LLMProvider,
        config: VeridianConfig,
        context_manager: Any | None = None,  # ContextManager | None
    ) -> None:
        self.provider = provider
        self.config = config
        self.context_manager = context_manager

    def run(
        self,
        task: Task,
        run_id: str = "",
        run_summary: str = "",
        attempt: int = 0,
    ) -> TaskResult:
        """
        Execute the worker loop for the given task.
        Returns a TaskResult with the parsed structured output (may be empty on timeout).
        """
        messages: list[dict[str, Any]] = self._build_initial_messages(
            task, run_id, run_summary, attempt
        )

        max_turns = self.config.max_turns_per_task
        raw_output = ""
        structured: dict[str, Any] = {}

        for turn in range(max_turns):
            msg_list = [Message(role=m["role"], content=m["content"]) for m in messages]

            response = self.provider.complete(msg_list)
            content = response.content
            raw_output += content + "\n"

            log.debug(
                "worker.turn task_id=%s turn=%d/%d finish=%s",
                task.id, turn + 1, max_turns, response.finish_reason,
            )

            # Check for result block
            match = _RESULT_RE.search(content)
            if match:
                try:
                    data = json.loads(match.group(1))
                    structured = data.get("structured", {})
                    if not isinstance(structured, dict):
                        structured = {}
                    log.info(
                        "worker.result_found task_id=%s turn=%d", task.id, turn + 1
                    )
                    return TaskResult(
                        raw_output=raw_output,
                        structured=structured,
                        artifacts=data.get("artifacts", []),
                        token_usage={
                            "input_tokens": response.input_tokens,
                            "output_tokens": response.output_tokens,
                            "total_tokens": response.total_tokens,
                        },
                    )
                except (json.JSONDecodeError, AttributeError) as exc:
                    log.warning("worker.result_parse_error task_id=%s err=%s", task.id, exc)

            # Append assistant response to conversation
            messages.append({"role": "assistant", "content": content})

            # Check for bash commands — if present, loop continues (executor handles)
            has_bash = bool(_BASH_RE.search(content))

            # If no result and no bash → nudge agent
            if not has_bash and not match:
                messages.append({"role": "user", "content": _NUDGE_MESSAGE})

        log.warning(
            "worker.max_turns_exceeded task_id=%s turns=%d", task.id, max_turns
        )
        return TaskResult(raw_output=raw_output, structured=structured)

    def _build_initial_messages(
        self,
        task: Task,
        run_id: str,
        run_summary: str,
        attempt: int,
    ) -> list[dict[str, Any]]:
        """Build initial message list from context manager or minimal fallback."""
        if self.context_manager:
            result: list[dict[str, Any]] = self.context_manager.build_worker_context(
                task,
                run_id=run_id,
                run_summary=run_summary,
                attempt=attempt,
            )
            return result
        # Minimal fallback when no context manager is injected
        description = task.description or task.title
        content = (
            f"Complete this task: {task.title}\n\n"
            f"{description}\n\n"
            f"Output your result as:\n"
            f'<veridian:result>{{"summary": "...", "structured": {{}}}}</veridian:result>'
        )
        if attempt > 0 and task.last_error:
            content += f"\n\n[RETRY ERROR]\n{str(task.last_error)[:300]}"

        return [
            {"role": "system", "content": "You are a Veridian AI agent."},
            {"role": "user", "content": content},
        ]
