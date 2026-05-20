from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_gate_example_demonstrates_verified_completion() -> None:
    result = subprocess.run(
        [sys.executable, "examples/release_gate.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "'done_count': 1" in result.stdout
    assert "'failed_count': 1" in result.stdout
    assert "Release gate: good evidence: done" in result.stdout
    assert "Release gate: missing evidence: failed" in result.stdout
