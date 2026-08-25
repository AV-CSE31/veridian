from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast

import pytest
from filelock import FileLock

from veridian import VerificationContract, VerifierStep, verify_completion
from veridian.core.contract import (
    append_decision_jsonl,
    latest_proof_hash,
    sign_decision,
    validate_proof_chain,
)
from veridian.core.exceptions import VeridianConfigError, VerificationError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult, VerifierRegistry
from veridian.verify.builtin.bash import BashExitCodeVerifier
from veridian.verify.builtin.repo_guard import RepoGuardVerifier

_SIGNING_KEY = "completion-proof-test-signing-key-v1"


class _LeakyVerifier(BaseVerifier):
    id: ClassVar[str] = "leaky-test-verifier"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        del task
        secret = result.structured["account_number"]
        return VerificationResult(
            passed=False,
            error=f"account {secret} was rejected",
            evidence={"submitted_account": secret},
        )


class _CrashingVerifier(BaseVerifier):
    id: ClassVar[str] = "crashing-test-verifier"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        del task, result
        raise RuntimeError("verifier infrastructure unavailable")


class _CountingVerifier(BaseVerifier):
    id: ClassVar[str] = "counting-test-verifier"
    calls: ClassVar[int] = 0

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        del task, result
        type(self).calls += 1
        return VerificationResult(passed=True)


class _MalformedVerifier(BaseVerifier):
    id: ClassVar[str] = "malformed-test-verifier"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        del task, result
        return cast(
            VerificationResult,
            SimpleNamespace(passed="false", error=None, evidence={}),
        )


@pytest.mark.parametrize(
    ("contract_id", "verifiers", "message"),
    [
        ("   ", [VerifierStep(verifier_id="schema")], "contract_id"),
        ("release_gate", [], "at least one"),
    ],
)
def test_invalid_contract_configuration_uses_veridian_error_hierarchy(
    contract_id: str, verifiers: list[VerifierStep], message: str
) -> None:
    with pytest.raises(VeridianConfigError, match=message):
        VerificationContract(contract_id=contract_id, verifiers=verifiers)


def test_verifier_step_requires_nonempty_identifier() -> None:
    with pytest.raises(VeridianConfigError, match="verifier_id"):
        VerifierStep(verifier_id="   ")


def test_verify_completion_writes_passing_proof(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={
                    "schema": {
                        "type": "object",
                        "required": ["decision"],
                        "properties": {"decision": {"const": "ship"}},
                    }
                },
            )
        ],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={"task": "decide release"},
        output_payload={"decision": "ship"},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )

    assert decision.passed is True
    assert decision.proof_hash
    assert decision.signature
    persisted = json.loads(proof_file.read_text(encoding="utf-8").splitlines()[0])
    assert persisted["contract_id"] == "release_gate"
    assert persisted["passed"] is True
    assert persisted["proof_hash"] == decision.proof_hash


def test_verify_completion_blocks_failed_verifier(tmp_path: Path) -> None:
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"required": ["decision"]}},
            )
        ],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={"task": "decide release"},
        output_payload={"risk": "low"},
        proof_file=tmp_path / "veridian-proof.jsonl",
        signing_key=_SIGNING_KEY,
        include_diagnostics=True,
    )

    assert decision.passed is False
    assert decision.blocking is True
    assert any("Schema validation failed" in item for item in decision.feedback)


def test_verify_completion_requires_operator_supplied_signing_key(monkeypatch) -> None:
    monkeypatch.delenv("VERIDIAN_PROOF_SIGNING_KEY", raising=False)
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )

    with pytest.raises(VeridianConfigError, match="VERIDIAN_PROOF_SIGNING_KEY"):
        verify_completion(
            contract=contract,
            input_payload={"task": "decide release"},
            output_payload={"decision": "ship"},
        )


def test_missing_signing_key_fails_before_any_verifier_runs(monkeypatch) -> None:
    monkeypatch.delenv("VERIDIAN_PROOF_SIGNING_KEY", raising=False)
    _CountingVerifier.calls = 0
    verifier_registry = VerifierRegistry()
    verifier_registry.register(_CountingVerifier)
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[VerifierStep(verifier_id=_CountingVerifier.id)],
    )

    with pytest.raises(VeridianConfigError):
        verify_completion(
            contract=contract,
            input_payload={},
            output_payload={},
            verifier_registry=verifier_registry,
        )

    assert _CountingVerifier.calls == 0


def test_verify_completion_accepts_explicit_signing_key() -> None:
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={"task": "decide release"},
        output_payload={"decision": "ship"},
        signing_key="s" * 32,
    )

    assert decision.signature


def test_verify_completion_rejects_short_signing_key() -> None:
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )

    with pytest.raises(VeridianConfigError, match="at least 32 bytes"):
        verify_completion(
            contract=contract,
            input_payload={"task": "decide release"},
            output_payload={"decision": "ship"},
            signing_key="too-short",
        )


def test_completion_proof_redacts_agent_payloads_by_default(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="payment_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={"api_key": "input-secret-123"},
        output_payload={"account_number": "output-secret-456"},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )

    persisted = proof_file.read_text(encoding="utf-8")
    assert "input-secret-123" not in persisted
    assert "output-secret-456" not in persisted
    assert decision.input_hash
    assert decision.output_hash
    assert "input_payload" not in decision.evidence
    assert "output_payload" not in decision.evidence


def test_completion_proof_redacts_verifier_diagnostics_by_default(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    verifier_registry = VerifierRegistry()
    verifier_registry.register(_LeakyVerifier)
    contract = VerificationContract(
        contract_id="payment_gate",
        verifiers=[VerifierStep(verifier_id=_LeakyVerifier.id)],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={"intent": "payment"},
        output_payload={"account_number": "GB82-WEST-SECRET"},
        proof_file=proof_file,
        verifier_registry=verifier_registry,
        signing_key=_SIGNING_KEY,
    )

    persisted = proof_file.read_text(encoding="utf-8")
    assert "GB82-WEST-SECRET" not in persisted
    assert decision.verifier_results[0]["evidence"] == {}
    assert decision.verifier_results[0]["evidence_hash"]
    assert decision.verifier_results[0]["error"] == "verifier reported failure"


def test_verifier_exception_cannot_be_downgraded_to_nonblocking_success() -> None:
    verifier_registry = VerifierRegistry()
    verifier_registry.register(_CrashingVerifier)
    contract = VerificationContract(
        contract_id="payment_gate",
        verifiers=[VerifierStep(verifier_id=_CrashingVerifier.id, blocking=False)],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        verifier_registry=verifier_registry,
        signing_key=_SIGNING_KEY,
    )

    assert decision.passed is False
    assert decision.blocking is True
    assert decision.verifier_results[0]["error"] == "verifier execution failed"


def test_malformed_verifier_result_cannot_be_truthy_success() -> None:
    verifier_registry = VerifierRegistry()
    verifier_registry.register(_MalformedVerifier)
    contract = VerificationContract(
        contract_id="payment_gate",
        verifiers=[VerifierStep(verifier_id=_MalformedVerifier.id, blocking=False)],
    )

    decision = verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        verifier_registry=verifier_registry,
        signing_key=_SIGNING_KEY,
    )

    assert decision.passed is False
    assert decision.blocking is True
    assert decision.verifier_results[0]["error"] == "verifier execution failed"


def test_completion_proof_binds_full_verifier_contract() -> None:
    first_contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"required": ["approved"]}},
            )
        ],
    )
    second_contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"required": ["amount"]}},
            )
        ],
    )

    first = verify_completion(
        contract=first_contract,
        input_payload={},
        output_payload={"approved": True},
        signing_key=_SIGNING_KEY,
    )
    second = verify_completion(
        contract=second_contract,
        input_payload={},
        output_payload={"amount": 10},
        signing_key=_SIGNING_KEY,
    )

    assert first.contract_hash
    assert second.contract_hash
    assert first.contract_hash != second.contract_hash


def test_completion_proof_chain_validates_every_signed_link(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    verify_completion(
        contract=contract,
        input_payload={"attempt": 1},
        output_payload={"decision": "hold"},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )
    second = verify_completion(
        contract=contract,
        input_payload={"attempt": 2},
        output_payload={"decision": "ship"},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )

    validation = validate_proof_chain(proof_file, signing_key=_SIGNING_KEY)

    assert validation.valid is True
    assert validation.checked_count == 2
    assert validation.head_hash == second.proof_hash
    assert validation.error is None


def test_trusted_head_detects_a_separately_valid_fork(tmp_path: Path) -> None:
    trusted_file = tmp_path / "trusted.jsonl"
    fork_file = tmp_path / "fork.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    trusted = verify_completion(
        contract=contract,
        input_payload={"attempt": "trusted"},
        output_payload={"decision": "ship"},
        proof_file=trusted_file,
        signing_key=_SIGNING_KEY,
    )
    verify_completion(
        contract=contract,
        input_payload={"attempt": "fork"},
        output_payload={"decision": "hold"},
        proof_file=fork_file,
        signing_key=_SIGNING_KEY,
    )

    unanchored = validate_proof_chain(fork_file, signing_key=_SIGNING_KEY)
    anchored = validate_proof_chain(
        fork_file,
        signing_key=_SIGNING_KEY,
        expected_head=trusted.proof_hash,
        expected_count=1,
    )

    assert unanchored.valid is True
    assert unanchored.anchored is False
    assert any("rollback" in limitation for limitation in unanchored.limitations)
    assert anchored.valid is False
    assert anchored.error == "proof head mismatch"


def test_append_rejects_replayed_decision_identifier(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    decision = verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        signing_key=_SIGNING_KEY,
    )
    append_decision_jsonl(proof_file, decision, signing_key=_SIGNING_KEY)

    with pytest.raises(VerificationError, match="duplicate decision_id"):
        append_decision_jsonl(proof_file, decision, signing_key=_SIGNING_KEY)


def test_append_surfaces_lock_contention_instead_of_blocking_forever(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    decision = verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        signing_key=_SIGNING_KEY,
    )
    lock = FileLock(str(proof_file) + ".lock")

    with lock, pytest.raises(VerificationError, match="timed out acquiring proof lock"):
        append_decision_jsonl(
            proof_file,
            decision,
            signing_key=_SIGNING_KEY,
            lock_timeout=0.01,
        )


def test_append_refuses_to_reset_chain_after_malformed_tail(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    verify_completion(
        contract=contract,
        input_payload={"attempt": 1},
        output_payload={"decision": "hold"},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )
    proof_file.write_text(
        proof_file.read_text(encoding="utf-8") + '{"truncated":', encoding="utf-8"
    )
    before = proof_file.read_bytes()

    with pytest.raises(VerificationError, match="existing completion proof chain is invalid"):
        verify_completion(
            contract=contract,
            input_payload={"attempt": 2},
            output_payload={"decision": "ship"},
            proof_file=proof_file,
            signing_key=_SIGNING_KEY,
        )

    assert proof_file.read_bytes() == before


def test_latest_proof_hash_fails_closed_for_malformed_chain(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    proof_file.write_text('{"truncated":', encoding="utf-8")

    with pytest.raises(VerificationError, match="completion proof chain is invalid"):
        latest_proof_hash(proof_file, signing_key=_SIGNING_KEY)


def test_proof_chain_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )
    original = proof_file.read_text(encoding="utf-8")
    proof_file.write_text(
        original.replace('"passed":true', '"passed":false,"passed":true'),
        encoding="utf-8",
    )

    validation = validate_proof_chain(proof_file, signing_key=_SIGNING_KEY)

    assert validation.valid is False
    assert "duplicate field" in (validation.error or "")


def test_proof_chain_rejects_unknown_unattested_field(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )
    payload = json.loads(proof_file.read_text(encoding="utf-8"))
    payload["approved_by"] = "CFO"
    proof_file.write_text(json.dumps(payload), encoding="utf-8")

    validation = validate_proof_chain(proof_file, signing_key=_SIGNING_KEY)

    assert validation.valid is False
    assert "unknown completion proof field" in (validation.error or "")


def test_proof_chain_rejects_wrong_signing_key(tmp_path: Path) -> None:
    proof_file = tmp_path / "veridian-proof.jsonl"
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    verify_completion(
        contract=contract,
        input_payload={},
        output_payload={},
        proof_file=proof_file,
        signing_key=_SIGNING_KEY,
    )

    validation = validate_proof_chain(proof_file, signing_key="x" * 32)

    assert validation.valid is False
    assert "signature mismatch" in (validation.error or "")


def test_sign_decision_refuses_inconsistent_embedded_payload_commitment() -> None:
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )
    decision = verify_completion(
        contract=contract,
        input_payload={"amount": 10},
        output_payload={"decision": "ship"},
        signing_key=_SIGNING_KEY,
        include_payloads=True,
    )
    inconsistent = replace(
        decision,
        evidence={**decision.evidence, "input_payload": {"amount": 1_000_000}},
    )

    with pytest.raises(VerificationError, match="embedded input payload hash mismatch"):
        sign_decision(inconsistent, signing_key=_SIGNING_KEY)


def test_completion_proof_rejects_non_finite_json_values() -> None:
    contract = VerificationContract(
        contract_id="release_gate",
        verifiers=[
            VerifierStep(
                verifier_id="schema",
                verifier_config={"schema": {"type": "object"}},
            )
        ],
    )

    with pytest.raises(VerificationError, match="not canonical JSON"):
        verify_completion(
            contract=contract,
            input_payload={},
            output_payload={},
            metadata={"risk_score": math.nan},
            signing_key=_SIGNING_KEY,
        )


def test_bash_verifier_runs_command_in_configured_cwd(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    marker.write_text("ok", encoding="utf-8")
    verifier = BashExitCodeVerifier(
        command=f"{sys.executable} -c \"from pathlib import Path; raise SystemExit(0 if Path('marker.txt').exists() else 2)\"",
        cwd=str(tmp_path),
    )

    result = verifier.verify(Task(title="cwd"), TaskResult(raw_output="done"))

    assert result.passed is True
    assert result.evidence["cwd"] == str(tmp_path)


def test_repo_guard_allows_expected_diff_and_blocks_secret(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "demo@example.com")
    _git(tmp_path, "config", "user.name", "Demo")
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "base")

    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    allowed = RepoGuardVerifier(repo_root=str(tmp_path), allowed_paths=["app.py"])
    allowed_result = allowed.verify(Task(title="guard"), TaskResult(raw_output="done"))
    assert allowed_result.passed is True
    snapshot_digest = allowed_result.evidence["repo_state_digest"]
    assert snapshot_digest.startswith("sha256:")
    repeated_result = allowed.verify(Task(title="guard"), TaskResult(raw_output="done"))
    assert repeated_result.evidence["repo_state_digest"] == snapshot_digest

    (tmp_path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    changed_result = allowed.verify(Task(title="guard"), TaskResult(raw_output="done"))
    assert changed_result.evidence["repo_state_digest"] != snapshot_digest

    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-testsecret1234567890\n", encoding="utf-8")
    blocked = RepoGuardVerifier(repo_root=str(tmp_path), allowed_paths=["app.py"])
    blocked_result = blocked.verify(Task(title="guard"), TaskResult(raw_output="done"))
    assert blocked_result.passed is False
    assert blocked_result.error is not None
    assert ".env" in blocked_result.error or "secret" in blocked_result.error.lower()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
