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
import time
from typing import TYPE_CHECKING, Any, ClassVar

from veridian.agents.base import BaseAgent
from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult, TraceStep
from veridian.providers.base import LLMProvider, LLMResponse, Message

# Lazy import: veridian.loop.__init__ eagerly loads runner + parallel_runner,
# which creates a worker ← runner cycle. Import ActivityJournal / run_activity
# directly from the submodule inside TYPE_CHECKING / at call time.
if TYPE_CHECKING:
    from veridian.loop.activity import ActivityJournal

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
        activity_journal: ActivityJournal | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.context_manager = context_manager
        # RV3-004: optional activity journal for side-effect replay safety.
        # When provided, provider.complete() calls route through run_activity
        # so cached results are returned on resume instead of re-invoking the
        # provider. The runner is responsible for seeding/snapshotting this.
        self.activity_journal = activity_journal

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
        start = time.perf_counter()
        raw_output = ""
        structured: dict[str, Any] = {}
        total_input_tokens = 0
        total_output_tokens = 0
        all_tool_calls: list[Any] = []
        trace_steps: list[TraceStep] = []

        for turn in range(max_turns):
            msg_list = [Message(role=m["role"], content=m["content"]) for m in messages]

            # RV3-004: route LLM call through the activity journal when one is
            # attached. Deterministic idempotency key ties each turn to
            # (task.id, attempt, turn) so resumes return cached responses.
            response = self._complete_with_activity(
                msg_list, task_id=task.id, attempt=attempt, turn=turn
            )
            content = response.content
            raw_output += content + "\n"
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            if response.tool_calls:
                all_tool_calls.extend(response.tool_calls)
            trace_steps.append(
                TraceStep(
                    step_id=f"turn_{turn + 1}",
                    role="assistant",
                    action_type="reason",
                    content=content,
                    timestamp_ms=int(time.time() * 1000),
                    token_count=response.total_tokens,
                )
            )

            log.debug(
                "worker.turn task_id=%s turn=%d/%d finish=%s",
                task.id,
                turn + 1,
                max_turns,
                response.finish_reason,
            )

            # Check for result block
            match = _RESULT_RE.search(content)
            if match:
                try:
                    data = json.loads(match.group(1))
                    structured = data.get("structured", {})
                    if not isinstance(structured, dict):
                        structured = {}
                    log.info("worker.result_found task_id=%s turn=%d", task.id, turn + 1)
                    worker_ms = (time.perf_counter() - start) * 1000
                    return TaskResult(
                        raw_output=raw_output,
                        structured=structured,
                        artifacts=data.get("artifacts", []),
                        trace_steps=trace_steps,
                        tool_calls=list(all_tool_calls),
                        timing={
                            "worker_ms": round(worker_ms, 1),
                            "worker_turns": turn + 1,
                        },
                        token_usage={
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens,
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

        log.warning("worker.max_turns_exceeded task_id=%s turns=%d", task.id, max_turns)
        worker_ms = (time.perf_counter() - start) * 1000
        return TaskResult(
            raw_output=raw_output,
            structured=structured,
            trace_steps=trace_steps,
            tool_calls=list(all_tool_calls),
            timing={"worker_ms": round(worker_ms, 1), "worker_turns": max_turns},
            token_usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            },
        )

    def _complete_with_activity(
        self,
        msg_list: list[Message],
        *,
        task_id: str,
        attempt: int,
        turn: int,
    ) -> LLMResponse:
        """Call provider.complete() optionally through the activity journal.

        Returns the LLMResponse. When an activity journal is attached, the call
        is wrapped in run_activity() so a resumed run returns the cached
        response instead of re-invoking the provider. The idempotency key is
        deterministic across restarts: ``llm_complete:{task_id}:a{attempt}:t{turn}``.
        """
        if self.activity_journal is None:
            return self.provider.complete(msg_list)

        # Lazy import to avoid circular: veridian.loop.__init__ pulls runners.
        from veridian.loop.activity import run_activity  # noqa: PLC0415

        key = f"llm_complete:{task_id}:a{attempt}:t{turn}"
        response_dict = run_activity(
            journal=self.activity_journal,
            fn=lambda: {
                "content": (r := self.provider.complete(msg_list)).content,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "model": r.model,
                "finish_reason": r.finish_reason,
                "tool_calls": r.tool_calls,
            },
            args=(),
            fn_name="provider.complete",
            idempotency_key=key,
        )
        return LLMResponse(
            content=str(response_dict.get("content", "")),
            input_tokens=int(response_dict.get("input_tokens", 0) or 0),
            output_tokens=int(response_dict.get("output_tokens", 0) or 0),
            model=str(response_dict.get("model", "") or ""),
            finish_reason=str(response_dict.get("finish_reason", "") or ""),
            tool_calls=list(response_dict.get("tool_calls", []) or []),
        )

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
