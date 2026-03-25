"""
veridian.core.config
─────────────────────
VeridianConfig — central configuration for the Veridian runner.
All fields have sensible defaults. Model is read from VERIDIAN_MODEL env var
if not set explicitly (per CLAUDE.md §7 rule 15: never hardcode model names).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["VeridianConfig"]

_DEFAULT_MODEL = "gemini/gemini-2.5-flash"


@dataclass
class VeridianConfig:
    """
    Central configuration for VeridianRunner and supporting components.

    All model-selection logic MUST read from this config — never hardcode
    model names anywhere else in the codebase.
    """

    # ── LLM ───────────────────────────────────────────────────────────────────
    model: str = field(
        default_factory=lambda: os.getenv("VERIDIAN_MODEL", _DEFAULT_MODEL)
    )
    temperature: float = 0.2
    max_tokens: int = 4096
    provider_timeout: int = 120

    # ── Runner ────────────────────────────────────────────────────────────────
    max_turns_per_task: int = 10          # WorkerAgent loop limit
    max_retries: int = 3                  # per-task retry budget
    dry_run: bool = False                 # assemble context only, no LLM calls

    # ── Storage ───────────────────────────────────────────────────────────────
    ledger_file: Path = field(default_factory=lambda: Path("ledger.json"))
    progress_file: Path = field(default_factory=lambda: Path("progress.md"))

    # ── Context ───────────────────────────────────────────────────────────────
    context_window_tokens: int = 8000    # token budget for context assembly
    compaction_threshold: float = 0.85   # trigger compaction at this fill %

    # ── Concurrency ───────────────────────────────────────────────────────────
    max_parallel: int = 1                # ParallelRunner semaphore bound

    # ── Cost guard ────────────────────────────────────────────────────────────
    max_cost_usd: float = 50.0

    # ── Observability ─────────────────────────────────────────────────────────
    trace_file: str | None = None     # JSONL trace output path
    dashboard_port: int = 7474           # monitoring dashboard port

    # ── Phase filter ─────────────────────────────────────────────────────────
    phase: str | None = None          # restrict run to this phase

    # ── Skill library (opt-in) ────────────────────────────────────────────────
    skill_library_path: str | None = None   # None = disabled
    skill_min_confidence: float = 0.70
    skill_max_retries: int = 1
    skill_top_k: int = 3

    # ── Drift detection (opt-in) ─────────────────────────────────────────────
    drift_history_file: str | None = None   # None = disabled
    drift_window: int = 10                  # runs to compare against
    drift_threshold: float = 0.15           # minimum change magnitude to flag
