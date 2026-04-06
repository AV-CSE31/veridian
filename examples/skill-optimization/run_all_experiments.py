"""
Run all Veridian × SkillNet × AutoResearch experiments (E-01 through E-09).

Usage:
    python examples/run_all_experiments.py [--skip-fixtures]

The script:
  1. Optionally regenerates fixtures (unless --skip-fixtures is passed).
  2. Runs all 9 experiments in sequence.
  3. Prints a rich summary table to stdout.
  4. Saves results/summary.json with all ExperimentResult objects.

Budget: experiments are designed to cost < $0.50 total in LLM credits
(E-09 optional SelfConsistency path would add ~$1–2 if API key present).
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_dotenv_file(path: Path) -> None:
    """Minimal .env loader with no external dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv_file(Path(".env"))


from examples.experiments.shared.config import RESULTS_DIR, ExperimentResult


def run_fixtures() -> None:
    """Generate fixture data if not already present."""
    from examples.experiments.shared.config import DATA_DIR

    if (DATA_DIR / "skills.json").exists() and (DATA_DIR / "queries.json").exists():
        print("[fixtures] Already present — skipping generation.")
        return
    print("[fixtures] Generating...")
    from examples.fixtures.generate_fixtures import main as gen_main

    gen_main()


def run_experiment(module_name: str, exp_id: str) -> ExperimentResult:
    """Import and run one experiment. Returns ExperimentResult or error stub."""
    import importlib

    print(f"\n{'─' * 60}")
    print(f"  Running {exp_id}...")
    print(f"{'─' * 60}")
    t0 = time.monotonic()
    try:
        mod = importlib.import_module(module_name)
        result = mod.run()
        elapsed = time.monotonic() - t0
        print(
            f"  [{exp_id}] completed in {elapsed:.1f}s  ->  {'PASS' if result.passed else 'FAIL'}"
        )
        return result
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"  [{exp_id}] ERROR after {elapsed:.1f}s: {exc}")
        traceback.print_exc()
        return ExperimentResult(
            experiment_id=exp_id,
            hypothesis="(error)",
            passed=False,
            primary_metric="error",
            primary_value=0.0,
            threshold=0.0,
            notes=f"Exception: {exc}",
        )


def print_summary_table(results: list[ExperimentResult]) -> None:
    """Print a rich ASCII summary table to stdout."""
    col_widths = [6, 55, 8, 10, 10, 8]
    headers = ["ID", "Hypothesis", "Status", "Metric", "Value", "Thresh"]

    def row(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(w) if i < len(cells) - 1 else cell
            for i, (cell, w) in enumerate(zip(cells, col_widths))
        )

    sep = "─" * (sum(col_widths) + len(col_widths) * 2)
    print(f"\n{'=' * (len(sep))}")
    print("  EXPERIMENT SUMMARY")
    print(f"{'=' * (len(sep))}")
    print(row(headers))
    print(sep)

    passed_count = 0
    for r in results:
        status = "[PASS]" if r.passed else "[FAIL]"
        if r.passed:
            passed_count += 1
        hyp_short = r.hypothesis[:53] + ".." if len(r.hypothesis) > 55 else r.hypothesis
        value_str = (
            f"{r.primary_value:.4f}"
            if r.primary_value != 0 or not r.notes.startswith("Exception")
            else "ERROR"
        )
        print(
            row(
                [
                    r.experiment_id,
                    hyp_short,
                    status,
                    r.primary_metric[:10],
                    value_str,
                    f"{r.threshold:.4f}",
                ]
            )
        )

    print(sep)
    print(f"  {passed_count}/{len(results)} experiments passed")
    print(f"{'=' * (len(sep))}")


def save_results(results: list[ExperimentResult]) -> Path:
    """Save results to results/summary.json atomically."""
    import tempfile

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "experiments": [r.to_dict() for r in results],
    }

    out_path = RESULTS_DIR / "summary.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=RESULTS_DIR, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        json.dump(summary, f, indent=2)
        tmp = f.name
    os.replace(tmp, str(out_path))
    return out_path


# ── Experiment registry ───────────────────────────────────────────────────────

EXPERIMENTS = [
    ("examples.experiments.e01_skill_trust_decay", "E-01"),
    ("examples.experiments.e02_static_vs_dynamic_confidence", "E-02"),
    ("examples.experiments.e03_semantic_grounding_retrieval", "E-03"),
    ("examples.experiments.e04_crossrun_consistency_drift", "E-04"),
    ("examples.experiments.e05_adversarial_skill_poisoning", "E-05"),
    ("examples.experiments.e06_trust_propagation", "E-06"),
    ("examples.experiments.e07_compliance_ontology", "E-07"),
    ("examples.experiments.e08_regulatory_amendment", "E-08"),
    ("examples.experiments.e09_e2e_ablation", "E-09"),
]


def main(skip_fixtures: bool = False) -> None:
    print("=" * 60)
    print("  Veridian × SkillNet × AutoResearch Experiment Suite")
    print("  Model: gemini/gemini-2.0-flash")
    print("=" * 60)

    if not skip_fixtures:
        run_fixtures()
    else:
        print("[fixtures] Skipped (--skip-fixtures).")

    total_t0 = time.monotonic()
    results: list[ExperimentResult] = []

    for module_name, exp_id in EXPERIMENTS:
        result = run_experiment(module_name, exp_id)
        results.append(result)

    total_elapsed = time.monotonic() - total_t0

    print_summary_table(results)

    out_path = save_results(results)
    print(f"\n  Results saved -> {out_path}")
    print(f"  Total wall time: {total_elapsed:.1f}s")


if __name__ == "__main__":
    skip = "--skip-fixtures" in sys.argv
    main(skip_fixtures=skip)
