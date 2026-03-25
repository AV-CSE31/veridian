"""
Shared configuration for the Veridian × SkillNet × AutoResearch experiment suite.

All paths are anchored to the examples/ directory so experiments are
relocatable regardless of where the repo is checked out.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ── Model ─────────────────────────────────────────────────────────────────────
GEMINI_MODEL: str = "gemini/gemini-2.0-flash"

# ── Directory layout ──────────────────────────────────────────────────────────
# examples/experiments/shared/config.py → .parent x3 → examples/
EXAMPLES_ROOT: Path = Path(__file__).parent.parent.parent.resolve()

RESULTS_DIR: Path = EXAMPLES_ROOT / "results"
FIXTURES_DIR: Path = EXAMPLES_ROOT / "fixtures"
DATA_DIR: Path = EXAMPLES_ROOT / "fixtures" / "data"

# Ensure output dirs exist at import time
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Budget guard ───────────────────────────────────────────────────────────────
MAX_LLM_BUDGET_USD: float = float(os.getenv("VERIDIAN_EXP_BUDGET", "10.0"))

# ── Seed for reproducibility ──────────────────────────────────────────────────
RANDOM_SEED: int = 42


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class ExperimentResult:
    """Standardised result container returned by every experiment."""

    experiment_id: str          # e.g. "E-01"
    hypothesis: str             # one-line hypothesis being tested
    passed: bool                # did the experiment confirm the hypothesis?
    primary_metric: str         # metric name (e.g. "silent_failure_rate")
    primary_value: float        # measured value
    threshold: float            # hypothesis threshold
    secondary_metrics: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis": self.hypothesis,
            "passed": self.passed,
            "primary_metric": self.primary_metric,
            "primary_value": round(self.primary_value, 4),
            "threshold": self.threshold,
            "secondary_metrics": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.secondary_metrics.items()
            },
            "notes": self.notes,
        }
