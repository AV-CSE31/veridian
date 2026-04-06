"""Prompt asset paths for built-in agent prompts."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
WORKER_PROMPT_FILE = PROMPTS_DIR / "worker.md"

__all__ = [
    "PROMPTS_DIR",
    "WORKER_PROMPT_FILE",
]
