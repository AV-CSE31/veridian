from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "benchmarks" / "sota_assurance_bench.py"


def _run(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_smoke_report_is_versioned_reproducible_and_discloses_limits() -> None:
    arguments = (
        "--profile",
        "smoke",
        "--iterations",
        "8",
        "--seed",
        "20260819",
        "--concurrency",
        "4",
    )

    first = _run(*arguments)
    second = _run(*arguments)

    assert first["schema_id"] == "veridian.assurance-benchmark-report.v1"
    assert first["harness_version"] == "1.0.0"
    assert first["passed"] is True
    assert first["totals"] == {
        "failures": 0,
        "iterations": 8,
        "passes": 8,
        "skipped": 0,
    }
    assert first["reproducibility_fingerprint"] == second["reproducibility_fingerprint"]
    assert set(first["results"]) == {
        "adapter_semantic_determinism",
        "banking_invariant_oracle",
        "canonical_mutation",
        "permit_context",
        "sqlite_concurrent_redemption",
        "sqlite_crash_recovery",
        "metamorphic_control",
        "trajectory_monitor",
    }
    assert first["environment"]["python_version"]
    assert first["environment"]["platform"]
    assert "not" in first["risk_statement"].lower()
    for result in first["results"].values():
        assert result["latency_ms"]["p50"] >= 0
        assert result["latency_ms"]["p95"] >= result["latency_ms"]["p50"]
        assert result["latency_ms"]["p99"] >= result["latency_ms"]["p95"]


def test_explicit_campaign_can_build_a_reproducible_100k_schedule_without_running_it() -> None:
    report = _run(
        "--profile",
        "campaign",
        "--iterations",
        "100000",
        "--seed",
        "7",
        "--dry-run",
    )

    assert report["dry_run"] is True
    assert sum(report["schedule_distribution"].values()) == 100_000
    assert report["config"]["iterations"] == 100_000
    assert report["reproducibility_fingerprint"].startswith("sha256:")
    assert report["limitations"]
