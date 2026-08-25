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
    # The schema verifier flags the missing required field. Assert on the field +
    # "required" rather than the exact validator message (Draft 2020-12 emits
    # "'reason' is a required property"), so the test is robust to message format.
    assert "reason" in result.stdout and "required" in result.stdout


def test_runner_release_gate_example_exports_valid_report() -> None:
    result = subprocess.run(
        [sys.executable, "examples/runner_release_gate.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "done=1 failed=0" in result.stdout
    assert "report_valid=True reports=1" in result.stdout
    assert "report_hash=" in result.stdout


def test_artifact_verification_gate_example_demonstrates_file_evidence() -> None:
    result = subprocess.run(
        [sys.executable, "examples/artifact_verification_gate.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "done=1 failed=0" in result.stdout
    assert "artifact_verified=True" in result.stdout


def test_coding_agent_verification_example_allows_fix_and_blocks_secret(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "coding_agent_verification_demo.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Positive path:" in result.stdout
    assert "  passed: True" in result.stdout
    assert "Negative path:" in result.stdout
    assert "  passed: False" in result.stdout
