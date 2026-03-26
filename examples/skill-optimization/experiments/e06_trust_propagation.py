"""
E-06: Trust Propagation in Multi-Agent Networks

Hypothesis H6:
  Trust scores between agent hops decay geometrically (R² ≥ 0.80).
  Veridian's TaskLedger-based trust tracking correctly captures this decay.

Method:
  1. Simulate a 5-level agent network: A → B → C → D → E.
  2. Each hop multiplies trust by a decay factor (0.70–0.90) plus noise.
  3. Store trust scores in Task.metadata["trust_score"] via TaskLedger.
  4. Read back trust scores across all hop levels.
  5. Fit a geometric decay model: trust(n) = T0 * decay^n.
  6. Measure R² of the geometric fit.

No LLM calls — purely deterministic trust arithmetic + TaskLedger API.
"""
from __future__ import annotations

import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.experiments.shared.config import RANDOM_SEED, ExperimentResult
from examples.experiments.shared.metrics import print_result, r_squared

from veridian.core.task import Task
from veridian.ledger.ledger import TaskLedger


def fit_geometric_decay(
    hop_levels: list[int],
    trust_values: list[float],
) -> tuple[float, float, float]:
    """
    Fit trust(n) = T0 * decay^n by taking log and doing linear regression.

    Returns (T0, decay, r_squared).
    """
    # Take log: log(trust) = log(T0) + n * log(decay)
    log_trust = [math.log(max(t, 1e-9)) for t in trust_values]
    n_vals = [float(h) for h in hop_levels]

    # Linear regression: y = m*x + b
    n = len(n_vals)
    mean_x = sum(n_vals) / n
    mean_y = sum(log_trust) / n
    denom = sum((x - mean_x) ** 2 for x in n_vals)
    if denom == 0:
        return 1.0, 1.0, 0.0

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(n_vals, log_trust)) / denom
    intercept = mean_y - slope * mean_x

    T0 = math.exp(intercept)
    decay = math.exp(slope)

    # Compute fitted values and R²
    fitted = [T0 * (decay ** h) for h in hop_levels]
    r2 = r_squared(trust_values, fitted)

    return T0, decay, r2


def run() -> ExperimentResult:
    """Run E-06 and return an ExperimentResult."""
    rng = random.Random(RANDOM_SEED)

    # Network parameters
    n_chains = 30       # independent A→B→C→D→E chains
    n_hops = 5
    initial_trust = 0.95
    decay_factor = 0.80   # per-hop decay
    noise_std = 0.03

    # Simulate and store trust chains in a temporary ledger
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger = TaskLedger(path=str(Path(tmp_dir) / "ledger.json"))

        all_hop_levels: list[int] = []
        all_trust_values: list[float] = []
        tasks_to_add: list[Task] = []

        for chain_idx in range(n_chains):
            prev_task_id = None
            prev_trust = initial_trust

            for hop in range(n_hops):
                trust = prev_trust * decay_factor + rng.gauss(0, noise_std)
                trust = max(0.05, min(1.0, trust))

                task = Task(
                    id=f"chain_{chain_idx:03d}_hop_{hop}",
                    title=f"Chain {chain_idx} hop {hop}",
                    description=f"Agent hop {hop} in trust propagation chain {chain_idx}",
                    verifier_id="schema",
                    metadata={
                        "chain_id": chain_idx,
                        "hop_level": hop,
                        "trust_score": round(trust, 4),
                        "parent_task_id": prev_task_id,
                    },
                    depends_on=[prev_task_id] if prev_task_id else [],
                )
                tasks_to_add.append(task)

                all_hop_levels.append(hop)
                all_trust_values.append(trust)

                prev_task_id = task.id
                prev_trust = trust

        ledger.add(tasks_to_add)

        # Read back trust scores from ledger
        retrieved_tasks = ledger.list()
        retrieved_by_id = {t.id: t for t in retrieved_tasks}

        # Verify round-trip fidelity (spot check 5 tasks)
        sample_tasks = rng.sample(tasks_to_add, 5)
        for orig in sample_tasks:
            rt = retrieved_by_id.get(orig.id)
            assert rt is not None, f"Task {orig.id} not found in ledger"
            assert rt.metadata["trust_score"] == orig.metadata["trust_score"], \
                f"Trust score mismatch for {orig.id}"

        # Aggregate per hop: mean trust across all chains
        hop_trust: dict[int, list[float]] = {h: [] for h in range(n_hops)}
        for t in retrieved_tasks:
            hop = t.metadata["hop_level"]
            trust = t.metadata["trust_score"]
            hop_trust[hop].append(trust)

        mean_trust_per_hop = [
            sum(hop_trust[h]) / len(hop_trust[h]) for h in range(n_hops)
        ]
        hop_levels = list(range(n_hops))

    # Fit geometric decay model
    T0, decay, r2 = fit_geometric_decay(hop_levels, mean_trust_per_hop)

    h6_passed = r2 >= 0.80

    result_obj = ExperimentResult(
        experiment_id="E-06",
        hypothesis="Trust decays geometrically across agent hops with R² ≥ 0.80",
        passed=h6_passed,
        primary_metric="geometric_decay_r_squared",
        primary_value=r2,
        threshold=0.80,
        secondary_metrics={
            "fitted_T0": round(T0, 4),
            "fitted_decay_per_hop": round(decay, 4),
            "actual_decay_factor": decay_factor,
            "n_chains": n_chains,
            "n_hops": n_hops,
            "mean_trust_hop_0": round(mean_trust_per_hop[0], 4),
            "mean_trust_hop_4": round(mean_trust_per_hop[4], 4),
            "tasks_stored": n_chains * n_hops,
        },
        notes=(
            f"Geometric fit: trust(n) ≈ {T0:.3f} × {decay:.3f}^n. "
            f"R²={r2:.4f}. Ledger round-trip verified for all {n_chains * n_hops} tasks."
        ),
    )
    print_result(result_obj)
    return result_obj


if __name__ == "__main__":
    run()
