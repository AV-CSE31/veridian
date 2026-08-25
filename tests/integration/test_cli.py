"""End-to-end tests for the supported ``veridian`` command."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from tests.assurance.test_attestation_contract import _proof

ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "veridian.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_command_writes_passing_machine_readable_result(tmp_path: Path) -> None:
    output_path = tmp_path / "verification.json"

    completed = _run_cli(
        "verify",
        "--verifier",
        "schema",
        "--verifier-config",
        '{"required_fields":["decision"]}',
        "--agent-output",
        '{"decision":"ship"}',
        "--task",
        "Release decision",
        "--output-path",
        str(output_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["passed"] is True
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "schema_version": "veridian-cli-result.v1",
        "passed": True,
        "verifier": "schema",
        "error": None,
        "evidence": {"schema_checks": "all passed", "fields_checked": 1},
    }


def test_verify_command_fails_closed_but_can_report_without_failing_job(tmp_path: Path) -> None:
    output_path = tmp_path / "blocked.json"
    args = (
        "verify",
        "--verifier",
        "schema",
        "--verifier-config",
        '{"required_fields":["decision"]}',
        "--agent-output",
        '{"claim":"ship"}',
        "--output-path",
        str(output_path),
    )

    blocked = _run_cli(*args)
    report_only = _run_cli(*args, "--no-fail-on-error")

    assert blocked.returncode == 1
    assert report_only.returncode == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert "required field 'decision'" in result["error"]


def test_verify_command_records_configuration_errors_as_denials(tmp_path: Path) -> None:
    output_path = tmp_path / "configuration-error.json"

    completed = _run_cli(
        "verify",
        "--verifier",
        "does-not-exist",
        "--verifier-config",
        "{}",
        "--agent-output",
        "claimed complete",
        "--output-path",
        str(output_path),
    )

    assert completed.returncode == 2
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["verifier"] == "does-not-exist"
    assert "not found" in result["error"]


def _write_proof_fixture(tmp_path: Path) -> tuple[Path, Path]:
    bundle, keys = _proof()
    bundle_path = tmp_path / "proof.bundle.json"
    keys_path = tmp_path / "verification-keys.json"
    bundle_path.write_bytes(bundle.to_bytes())
    keys_path.write_text(
        json.dumps(
            {
                "schema_id": "veridian.verification-keys.v1",
                "keys": [
                    {
                        "key_id": key_id,
                        "algorithm": algorithm,
                        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
                    }
                    for (key_id, algorithm), public_key in keys.keys.items()
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return bundle_path, keys_path


def test_verify_receipt_checks_a_portable_bundle_in_a_separate_process(tmp_path: Path) -> None:
    bundle_path, keys_path = _write_proof_fixture(tmp_path)
    output_path = tmp_path / "proof-result.json"

    completed = _run_cli(
        "verify-receipt",
        "--bundle",
        str(bundle_path),
        "--keys",
        str(keys_path),
        "--output-path",
        str(output_path),
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == json.loads(completed.stdout)
    assert result["valid"] is True
    assert result["verified_signer_ids"] == ["receipt-key-2026-08"]
    assert result["replay_status"] == "not-checked"
    assert result["history_status"] == "unanchored"


def test_verify_receipt_rejects_bound_object_substitution(tmp_path: Path) -> None:
    bundle, _ = _proof()
    bundle_path, keys_path = _write_proof_fixture(tmp_path)
    bundle_path.write_bytes(replace(bundle, contract_bytes=b"{}").to_bytes())

    completed = _run_cli(
        "verify-receipt",
        "--bundle",
        str(bundle_path),
        "--keys",
        str(keys_path),
    )

    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["valid"] is False
    assert result["error_code"] == "contract-binding-mismatch"


def test_verify_receipt_rejects_malformed_trust_roots_as_configuration_error(
    tmp_path: Path,
) -> None:
    bundle_path, keys_path = _write_proof_fixture(tmp_path)
    keys_path.write_text(
        '{"schema_id":"veridian.verification-keys.v1","keys":[]}',
        encoding="utf-8",
    )

    completed = _run_cli(
        "verify-receipt",
        "--bundle",
        str(bundle_path),
        "--keys",
        str(keys_path),
    )

    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["valid"] is False
    assert "at least one" in result["error"]
