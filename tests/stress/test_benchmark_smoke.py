"""
tests.stress.test_benchmark_smoke
---------------------------------------------------------------------
Smoke tests for the reliability benchmarks in benchmarks/. Each runs the
script end-to-end with small parameters and asserts the JSON report shows
zero reliability violations.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_bench(script: str, *args: str, env: dict[str, str] | None = None) -> dict:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "benchmarks" / script), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_crash_recovery_bench_no_loss_or_corruption() -> None:
    report = _run_bench(
        "crash_recovery_bench.py", "--runs", "3", "--min-kill-ms", "20", "--max-kill-ms", "120"
    )
    assert report["passed"] is True
    assert report["lost_operations"] == report["lost_ops"] == 0
    assert report["corrupted_runs"] == report["corruption_runs"] == 0
    assert report["acknowledged_operations"] == report["acked_ops"] > 0
    assert report["orphan_temp_files"] == report["orphan_tmp_files"]
    assert report["recovered_active_tasks"] == report["recovered_in_progress"]
    assert report["losses"] == report["loss_detail"]


def test_crash_recovery_bench_wal_mode_no_loss_or_corruption() -> None:
    report = _run_bench(
        "crash_recovery_bench.py",
        "--runs",
        "3",
        "--min-kill-ms",
        "20",
        "--max-kill-ms",
        "120",
        env={"VERIDIAN_LEDGER_WAL": "1"},
    )
    assert report["passed"] is True
    assert report["lost_operations"] == report["lost_ops"] == 0
    assert report["corrupted_runs"] == report["corruption_runs"] == 0
    assert report["acknowledged_operations"] == report["acked_ops"] > 0
    assert report["orphan_temp_files"] == report["orphan_tmp_files"]
    assert report["recovered_active_tasks"] == report["recovered_in_progress"]
    assert report["losses"] == report["loss_detail"]


def test_verified_completion_bench_gate_catches_all_defects() -> None:
    report = _run_bench(
        "verified_completion_bench.py", "--tasks", "40", "--defect-rate", "0.3", "--seed", "11"
    )
    assert report["passed"] is True
    assert report["gated"]["false_done"] == 0
    assert report["gated"]["caught"] == report["defective_results"]
    # The trust-the-claim baseline must exhibit the failure the gate prevents.
    assert report["baseline"]["false_done"] == report["defective_results"]
