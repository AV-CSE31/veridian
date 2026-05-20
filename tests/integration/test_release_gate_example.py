from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_decorator_release_gate_example_demonstrates_verified_completion() -> None:
    result = subprocess.run(
        [sys.executable, "examples/decorator_release_gate.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "good passed=True" in result.stdout
    assert "bad passed=False" in result.stdout
    assert "required field 'reason' is missing or null" in result.stdout
