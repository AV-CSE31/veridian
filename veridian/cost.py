"""
veridian.cost
─────────────
A3: Token counting and cost attribution as first-class fields.

Usage::

    from veridian.cost import CostTracker

    tracker = CostTracker(run_id="run-001")
    tracker.record(
        task_id="t-001",
        model="claude-sonnet-4-6",
        input_tokens=1500,
        output_tokens=300,
    )
    print(f"Total cost: ${tracker.total_usd:.4f}")
    print(f"Total tokens: {tracker.total_tokens}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "BUILTIN_PRICING",
    "CostEntry",
    "CostTracker",
    "ModelPricing",
    "compute_cost",
]


# ── ModelPricing ──────────────────────────────────────────────────────────────


@dataclass
class ModelPricing:
    """Per-token pricing for one model (prices in USD per 1 000 tokens)."""

    input_per_1k: float  # USD per 1 000 input tokens
    output_per_1k: float  # USD per 1 000 output tokens

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Compute the USD cost for a single LLM call."""
        return (input_tokens * self.input_per_1k + output_tokens * self.output_per_1k) / 1000.0


# ── Built-in pricing table ────────────────────────────────────────────────────
# Prices as of Q1 2026 (USD / 1 000 tokens).  Update when providers change rates.

BUILTIN_PRICING: dict[str, ModelPricing] = {
    # Anthropic
    "claude-opus-4-6": ModelPricing(input_per_1k=15.0, output_per_1k=75.0),
    "claude-opus-4-5": ModelPricing(input_per_1k=15.0, output_per_1k=75.0),
    "claude-sonnet-4-6": ModelPricing(input_per_1k=3.0, output_per_1k=15.0),
    "claude-sonnet-4-5": ModelPricing(input_per_1k=3.0, output_per_1k=15.0),
    "claude-haiku-4-5": ModelPricing(input_per_1k=0.25, output_per_1k=1.25),
    "claude-3-5-sonnet-20241022": ModelPricing(input_per_1k=3.0, output_per_1k=15.0),
    "claude-3-5-haiku-20241022": ModelPricing(input_per_1k=0.80, output_per_1k=4.0),
    "claude-3-opus-20240229": ModelPricing(input_per_1k=15.0, output_per_1k=75.0),
    # OpenAI
    "gpt-4o": ModelPricing(input_per_1k=2.5, output_per_1k=10.0),
    "gpt-4o-mini": ModelPricing(input_per_1k=0.15, output_per_1k=0.60),
    "gpt-4-turbo": ModelPricing(input_per_1k=10.0, output_per_1k=30.0),
    "gpt-4": ModelPricing(input_per_1k=30.0, output_per_1k=60.0),
    "gpt-3.5-turbo": ModelPricing(input_per_1k=0.50, output_per_1k=1.50),
    # Google
    "gemini-1.5-pro": ModelPricing(input_per_1k=3.5, output_per_1k=10.5),
    "gemini-1.5-flash": ModelPricing(input_per_1k=0.075, output_per_1k=0.30),
    "gemini-2.0-flash": ModelPricing(input_per_1k=0.10, output_per_1k=0.40),
}

# Fallback pricing for unknown models
_FALLBACK_PRICING = ModelPricing(input_per_1k=1.0, output_per_1k=3.0)


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Compute USD cost for a model call.

    Exact match is tried first, then a prefix/substring match against the
    built-in table.  If no match is found, a conservative fallback rate is used.
    """
    if model in BUILTIN_PRICING:
        return BUILTIN_PRICING[model].cost_usd(input_tokens, output_tokens)

    model_lower = model.lower()
    for key, pricing in BUILTIN_PRICING.items():
        if key in model_lower or model_lower.startswith(key.lower()):
            return pricing.cost_usd(input_tokens, output_tokens)

    return _FALLBACK_PRICING.cost_usd(input_tokens, output_tokens)


# ── CostEntry ─────────────────────────────────────────────────────────────────


@dataclass
class CostEntry:
    """Cost record for a single LLM call within a task."""

    task_id: str
    run_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        """Total input + output tokens."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


# ── CostTracker ───────────────────────────────────────────────────────────────


class CostTracker:
    """
    Accumulates token usage and cost across all tasks in a run.

    Usage::

        tracker = CostTracker(run_id="run-001")
        tracker.record(task_id="t1", model="gpt-4", input_tokens=1000, output_tokens=500)
        print(tracker.total_usd, tracker.total_tokens)
    """

    def __init__(self, run_id: str) -> None:
        """Initialize tracker for one run."""
        self.run_id = run_id
        self.entries: list[CostEntry] = []

    def record(
        self,
        task_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CostEntry:
        """Add a cost entry for one LLM call.  Returns the created entry."""
        cost = compute_cost(model, input_tokens, output_tokens)
        entry = CostEntry(
            task_id=task_id,
            run_id=self.run_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.entries.append(entry)
        return entry

    @property
    def total_usd(self) -> float:
        """Sum of all cost entries in USD."""
        return sum(e.cost_usd for e in self.entries)

    @property
    def total_tokens(self) -> int:
        """Sum of all token counts across all entries."""
        return sum(e.total_tokens for e in self.entries)

    def by_task(self) -> dict[str, list[CostEntry]]:
        """Return entries grouped by task_id."""
        groups: dict[str, list[CostEntry]] = {}
        for entry in self.entries:
            groups.setdefault(entry.task_id, []).append(entry)
        return groups

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "run_id": self.run_id,
            "total_usd": self.total_usd,
            "total_tokens": self.total_tokens,
            "entries": [e.to_dict() for e in self.entries],
        }
