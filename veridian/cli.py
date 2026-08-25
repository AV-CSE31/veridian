"""Command-line boundary for deterministic, one-shot verification."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from veridian.assurance import (
    AssuranceError,
    ProofBundleV1,
    StaticKeyProvider,
    verify_proof_bundle,
)
from veridian.core.atomic_io import atomic_write_json
from veridian.core.exceptions import VeridianConfigError, VeridianError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import registry

CLI_RESULT_SCHEMA_VERSION = "veridian-cli-result.v1"
CLI_PROOF_RESULT_SCHEMA_VERSION = "veridian-cli-proof-verification.v1"


def _json_object(raw: str, option: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VeridianConfigError(f"{option} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise VeridianConfigError(f"{option} must decode to a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veridian",
        description="Deterministically verify an AI agent output.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="run one registered verifier")
    verify.add_argument("--verifier", default="schema", help="registered verifier ID")
    verify.add_argument(
        "--verifier-config",
        default=(
            '{"schema":{"type":"object","required":["output"],'
            '"properties":{"output":{"type":"string","minLength":1}}}}'
        ),
        help="verifier constructor configuration as a JSON object",
    )
    verify.add_argument("--agent-output", default="", help="agent output text or JSON object")
    verify.add_argument("--task", default="Agent output verification", help="task description")
    verify.add_argument(
        "--output-path",
        type=Path,
        default=Path("veridian-result.json"),
        help="machine-readable result path",
    )
    verify.add_argument(
        "--no-fail-on-error",
        action="store_true",
        help="return zero after writing a failed verification result",
    )
    verify_receipt = subparsers.add_parser(
        "verify-receipt",
        help="independently verify a portable proof bundle with explicit public keys",
    )
    verify_receipt.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="path to an exact ProofBundleV1 byte container",
    )
    verify_receipt.add_argument(
        "--keys",
        type=Path,
        required=True,
        help="path to a veridian.verification-keys.v1 JSON trust-root file",
    )
    verify_receipt.add_argument(
        "--output-path",
        type=Path,
        help="optional machine-readable verification result path",
    )
    return parser


def _verify(args: argparse.Namespace) -> int:
    raw_output = str(args.agent_output)
    verifier_id = str(args.verifier)
    try:
        config = _json_object(str(args.verifier_config), "--verifier-config")
        try:
            decoded_output = json.loads(raw_output)
        except json.JSONDecodeError:
            decoded_output = {"output": raw_output}
        structured = decoded_output if isinstance(decoded_output, dict) else {"output": raw_output}

        task = Task(
            title=str(args.task),
            description=str(args.task),
            verifier_id=verifier_id,
            verifier_config=config,
        )
        verification = registry.get(task.verifier_id, config or None).verify(
            task,
            TaskResult(raw_output=raw_output, structured=structured),
        )
    except VeridianError as exc:
        result = {
            "schema_version": CLI_RESULT_SCHEMA_VERSION,
            "passed": False,
            "verifier": verifier_id,
            "error": str(exc)[:300],
            "evidence": {},
        }
        atomic_write_json(args.output_path, result)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2

    result = {
        "schema_version": CLI_RESULT_SCHEMA_VERSION,
        "passed": verification.passed,
        "verifier": task.verifier_id,
        "error": verification.error,
        "evidence": verification.evidence,
    }
    atomic_write_json(args.output_path, result)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if verification.passed or args.no_fail_on_error else 1


def _verification_keys(path: Path) -> StaticKeyProvider:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VeridianConfigError(f"cannot read verification keys: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VeridianConfigError(f"verification keys must be UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_id", "keys"}:
        raise VeridianConfigError("verification keys require exactly 'schema_id' and 'keys'")
    if payload["schema_id"] != "veridian.verification-keys.v1":
        raise VeridianConfigError("unsupported verification-key schema")
    raw_keys = payload["keys"]
    if not isinstance(raw_keys, list) or not raw_keys:
        raise VeridianConfigError("verification keys require at least one public key")

    keys: dict[tuple[str, str], bytes] = {}
    for index, raw_key in enumerate(raw_keys):
        if not isinstance(raw_key, dict) or set(raw_key) != {
            "key_id",
            "algorithm",
            "public_key_b64",
        }:
            raise VeridianConfigError(f"verification key {index} has invalid fields")
        key_id = raw_key["key_id"]
        algorithm = raw_key["algorithm"]
        encoded = raw_key["public_key_b64"]
        if not all(isinstance(value, str) and value for value in (key_id, algorithm, encoded)):
            raise VeridianConfigError(f"verification key {index} contains an invalid string")
        if algorithm != "ed25519":
            raise VeridianConfigError(f"verification key {index} uses an unsupported algorithm")
        try:
            public_key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VeridianConfigError(f"verification key {index} contains invalid base64") from exc
        if len(public_key) != 32:
            raise VeridianConfigError(
                f"verification key {index} must contain a 32-byte Ed25519 public key"
            )
        identity = (key_id, algorithm)
        if identity in keys:
            raise VeridianConfigError(f"verification key {index} duplicates a key identity")
        keys[identity] = public_key
    return StaticKeyProvider(keys)


def _emit_proof_result(result: dict[str, object], output_path: Path | None) -> None:
    if output_path is not None:
        atomic_write_json(output_path, result)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def _verify_receipt(args: argparse.Namespace) -> int:
    try:
        try:
            bundle_bytes = args.bundle.read_bytes()
        except OSError as exc:
            raise VeridianConfigError(f"cannot read proof bundle: {exc}") from exc
        bundle = ProofBundleV1.from_bytes(bundle_bytes)
        keys = _verification_keys(args.keys)
    except (AssuranceError, VeridianConfigError) as exc:
        result: dict[str, object] = {
            "schema_version": CLI_PROOF_RESULT_SCHEMA_VERSION,
            "valid": False,
            "error_code": "configuration-error",
            "error": str(exc)[:500],
            "decision_digest": None,
            "receipt_id": None,
            "verified_signer_ids": [],
            "replay_status": "not-checked",
            "history_status": "unanchored",
        }
        _emit_proof_result(result, args.output_path)
        return 2

    verification = verify_proof_bundle(bundle, keys)
    result = {
        "schema_version": CLI_PROOF_RESULT_SCHEMA_VERSION,
        "valid": verification.valid,
        "error_code": (None if verification.error_code is None else verification.error_code.value),
        "error": verification.error,
        "decision_digest": verification.decision_digest,
        "receipt_id": verification.receipt_id,
        "verified_signer_ids": list(verification.verified_signer_ids),
        "replay_status": verification.replay_status.value,
        "history_status": verification.history_status.value,
    }
    _emit_proof_result(result, args.output_path)
    return 0 if verification.valid else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command and return a process exit status."""
    args = _parser().parse_args(argv)
    if args.command == "verify":
        return _verify(args)
    if args.command == "verify-receipt":
        return _verify_receipt(args)
    raise VeridianConfigError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
