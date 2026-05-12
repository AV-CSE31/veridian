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

__all__ = ["VeridianConfig", "default_data_dir"]

_DEFAULT_MODEL = "gemini/gemini-2.5-flash"


def default_data_dir() -> Path:
    """Resolve the directory under which ledger/progress files live by default.

    Container-friendly resolution order:

    1. ``VERIDIAN_DATA_DIR`` env var, if set. Created if absent.
    2. The current working directory (backwards-compatible default for
       existing scripts and unit tests).

    Set ``VERIDIAN_DATA_DIR=/var/lib/veridian`` in production containers
    to persist state on a mounted volume rather than the ephemeral PWD.
    """
    override = os.getenv("VERIDIAN_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


@dataclass
class VeridianConfig:
    """
    Central configuration for VeridianRunner and supporting components.

    All model-selection logic MUST read from this config — never hardcode
    model names anywhere else in the codebase.
    """

    # ── LLM ───────────────────────────────────────────────────────────────────
    model: str = field(default_factory=lambda: os.getenv("VERIDIAN_MODEL", _DEFAULT_MODEL))
    temperature: float = 0.2
    max_tokens: int = 4096
    provider_timeout: int = 120

    # ── Runner ────────────────────────────────────────────────────────────────
    max_turns_per_task: int = 10  # WorkerAgent loop limit
    max_retries: int = 3  # per-task retry budget
    dry_run: bool = False  # assemble context only, no LLM calls

    # RV3-001: Resume PAUSED tasks before fetching new PENDING work. Keeps HITL
    # approvals from being starved by newly queued tasks.
    resume_paused_on_start: bool = True

    # RV3-003: Fail-closed when a replay snapshot (model/prompt/verifier config)
    # changes between runs for the same task.
    strict_replay: bool = True

    # RV3-004/005: Enable the activity journal side-effect boundary. When True,
    # worker provider.complete() calls route through run_activity() so retries
    # return cached results instead of re-executing.
    activity_journal_enabled: bool = True

    # ── Storage ───────────────────────────────────────────────────────────────
    storage_backend: str = "ledger"  # "ledger" | "local_json" | "redis" | "postgres"
    # Defaults are anchored to default_data_dir() so containers can persist
    # state by setting ``VERIDIAN_DATA_DIR=/var/lib/veridian``. Bare paths
    # like ``ledger.json`` continue to resolve relative to that root.
    ledger_file: Path = field(default_factory=lambda: default_data_dir() / "ledger.json")
    progress_file: Path = field(default_factory=lambda: default_data_dir() / "progress.md")
    # FileLock acquire timeout for ledger writes. Tight enough that a
    # crashed peer with a stale lock surfaces a clear Timeout instead of
    # blocking pod startup indefinitely.
    ledger_lock_timeout: float = 15.0

    # ── Context ───────────────────────────────────────────────────────────────
    context_window_tokens: int = 8000  # token budget for context assembly
    compaction_threshold: float = 0.85  # trigger compaction at this fill %

    # ── Concurrency ───────────────────────────────────────────────────────────
    max_parallel: int = 1  # ParallelRunner semaphore bound

    # ── Cost guard ────────────────────────────────────────────────────────────
    max_cost_usd: float = 50.0

    # ── Observability ─────────────────────────────────────────────────────────
    trace_file: str | None = None  # JSONL trace output path
    dashboard_port: int = 7474  # monitoring dashboard port

    # ── Phase filter ─────────────────────────────────────────────────────────
    phase: str | None = None  # restrict run to this phase

    # ── Skill library (opt-in) ────────────────────────────────────────────────
    skill_library_path: str | None = None  # None = disabled
    skill_min_confidence: float = 0.70
    skill_max_retries: int = 1
    skill_top_k: int = 3

    # ── Drift detection (opt-in) ─────────────────────────────────────────────
    drift_history_file: str | None = None  # None = disabled
    drift_window: int = 10  # runs to compare against
    drift_threshold: float = 0.15  # minimum change magnitude to flag

    # ── Evolution safety (opt-in, Phase 7b) ──────────────────────────────────
    evolution_monitor_file: str | None = None  # None = disabled
    evolution_safety_threshold: float = 0.10  # per-pathway max degradation
    evolution_refusal_baseline: float = 0.95  # expected safety refusal rate
    fingerprint_history_file: str | None = None  # None = disabled
    fingerprint_similarity_threshold: float = 0.85  # cosine below this = alert
    canary_suite_path: str | None = None  # None = disabled

    # ── Secrets management (opt-in, Phase 8) ─────────────────────────────
    secrets_env_prefix: str = "VERIDIAN_"  # prefix for EnvSecretsProvider
    identity_guard_enabled: bool = True  # enable IdentityGuardHook
