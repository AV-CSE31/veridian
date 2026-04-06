"""
veridian.context.manager
─────────────────────────
ContextManager — assembles the worker agent prompt in a frozen 6-block order.

BLOCK ORDER IS A FROZEN CONTRACT (CLAUDE.md §2.4). Do NOT reorder.
  1. [SYSTEM]       worker.md system prompt      — always included, never compacted
  2. [ORIENTATION]  run summary + progress.md tail
  3. [TASK]         title, description, verifier_id, required_fields
  4. [RETRY ERROR]  last_error ≤ 300 chars       — ONLY if attempt > 0
  5. [ENVIRONMENT]  context_files                — ONLY if token budget allows
  6. [OUTPUT FMT]   veridian:result XML format
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from veridian.context.compactor import ContextCompactor
from veridian.context.window import TokenWindow

__all__ = ["ContextManager"]

log = logging.getLogger(__name__)

_WORKER_PROMPT_PATH = Path(__file__).parent.parent / "agents" / "prompts" / "worker.md"

_OUTPUT_FORMAT = """\
Output your final answer inside this exact XML block. No content after it.

<veridian:result>
{"summary": "<one sentence>", "structured": {<required fields>}, "artifacts": []}
</veridian:result>"""

_FALLBACK_SYSTEM = (
    "You are a Veridian AI agent. Complete the assigned task precisely and output "
    "your result in the specified XML format."
)


class ContextManager:
    """
    Assembles the worker agent context in the frozen 6-block order.

    Usage::

        cm = ContextManager(window=TokenWindow(8000))
        messages = cm.build_worker_context(task, run_id="r1", attempt=0)
        # → [{"role": "system", ...}, {"role": "user", ...}]
    """

    def __init__(
        self,
        window: TokenWindow | None = None,
        provider: Any | None = None,
        progress_path: Path | None = None,
    ) -> None:
        self.window = window or TokenWindow(capacity=8000)
        self._provider = provider
        self._progress_path = progress_path
        self._compactor = ContextCompactor(self.window, provider)

    def build_worker_context(
        self,
        task: Any,
        run_id: str = "",
        run_summary: str = "",
        attempt: int = 0,
    ) -> list[dict[str, str]]:
        """
        Build the 6-block worker context in frozen order.

        Returns a list of message dicts: [{"role": ..., "content": ...}].
        """
        self.window.reset()
        messages: list[dict[str, str]] = []

        # ── Block 1: [SYSTEM] — always included, never compacted ──────────────
        system_content = self._load_system_prompt()
        messages.append({"role": "system", "content": system_content})
        self.window.consume(self._count(system_content))

        # ── Build user message from blocks 2–6 ────────────────────────────────
        parts: list[str] = []

        # ── Block 2: [ORIENTATION] ────────────────────────────────────────────
        parts.append(self._build_orientation(run_id, run_summary))

        # ── Block 3: [TASK] ───────────────────────────────────────────────────
        parts.append(self._build_task_block(task))

        # ── Block 4: [RETRY ERROR] — only when attempt > 0 ───────────────────
        if attempt > 0:
            last_error = getattr(task, "last_error", None) or ""
            if last_error:
                error_text = str(last_error)[:300]
                parts.append(f"[RETRY ERROR]\n{error_text}")

        # ── Block 5: [ENVIRONMENT] — only if budget allows ───────────────────
        metadata: dict[str, Any] = getattr(task, "metadata", {}) or {}
        context_files: list[str] = metadata.get("context_files", []) or []
        if context_files:
            env_block = self._build_environment_block(context_files)
            if env_block:
                parts.append(env_block)

        # ── Block 6: [OUTPUT FMT] — always included ───────────────────────────
        parts.append(f"[OUTPUT FMT]\n{_OUTPUT_FORMAT}")

        user_content = "\n\n".join(parts)
        messages.append({"role": "user", "content": user_content})
        self.window.consume(self._count(user_content))

        return messages

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load worker.md; fall back to minimal prompt if not found."""
        try:
            if _WORKER_PROMPT_PATH.exists():
                return _WORKER_PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as exc:
            log.debug("context.system_prompt_load_failed err=%s", exc)
        return _FALLBACK_SYSTEM

    def _build_orientation(self, run_id: str, run_summary: str) -> str:
        """Build [ORIENTATION] block from run_id, summary, and progress.md."""
        parts = ["[ORIENTATION]"]
        if run_id:
            parts.append(f"Run ID: {run_id}")
        if run_summary:
            parts.append(run_summary)
        # Last 5 lines of progress.md
        if self._progress_path and self._progress_path.exists():
            try:
                lines = self._progress_path.read_text(encoding="utf-8").splitlines()
                recent = lines[-5:]
                if recent:
                    parts.append("Recent progress:\n" + "\n".join(recent))
            except Exception as exc:
                log.debug("context.progress_read_failed err=%s", exc)
        return "\n".join(parts)

    def _build_task_block(self, task: Any) -> str:
        """Build [TASK] block from task fields."""
        lines = [
            "[TASK]",
            f"ID: {getattr(task, 'id', '?')}",
            f"Title: {getattr(task, 'title', '')}",
        ]
        description = getattr(task, "description", "")
        if description:
            lines.append(f"Description: {description}")
        verifier_id = getattr(task, "verifier_id", "")
        if verifier_id:
            lines.append(f"Verifier: {verifier_id}")
        config: dict[str, Any] = getattr(task, "verifier_config", {}) or {}
        required = config.get("required_fields")
        if required:
            lines.append(f"Required fields: {', '.join(required)}")
        return "\n".join(lines)

    def _build_environment_block(self, context_files: list[str]) -> str:
        """Build [ENVIRONMENT] block from context_files, respecting token budget."""
        parts = ["[ENVIRONMENT]"]
        for fpath in context_files:
            try:
                content = Path(fpath).read_text(encoding="utf-8")
                tokens = self._count(content)
                if self.window.fits(tokens):
                    parts.append(f"--- {fpath} ---\n{content}")
                    self.window.consume(tokens)
                else:
                    log.info(
                        "context.file_skipped path=%s tokens=%d (budget exceeded)",
                        fpath,
                        tokens,
                    )
                    break
            except Exception as exc:
                log.debug("context.file_read_error path=%s err=%s", fpath, exc)
        return "\n\n".join(parts) if len(parts) > 1 else ""

    def _count(self, text: str) -> int:
        """Estimate token count via provider or character approximation."""
        if self._provider:
            try:
                return int(self._provider.count_tokens(text))
            except Exception:
                pass
        return int(max(1, len(text) // 4))
