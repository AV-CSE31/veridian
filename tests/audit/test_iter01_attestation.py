"""
ADVERSARIAL AUDIT — Iteration 1: Core value prop + the attestation wedge.

Each test encodes the PROPERTY THE README PROMISES. A failing test proves the
promise is a lie. Run: pytest tests/audit/test_iter01_attestation.py -v

Findings:
  I1-1 (P0): "hashable verification reports for audit trails" — the chain is an
             UNSIGNED sha256 chain. The holder (the audited party) can forge the
             entire chain with Veridian's OWN public API. validate_report_chain
             returns valid=True on a tampered chain.
  I1-2 (P1): the audit trail is opt-in (report_file defaults to None). A user
             following the README Quick Start produces NO durable evidence chain.
  I1-3 (P1): ConfidenceScore is a fabricated number with a dead parameter
             (max_retries) that changes nothing, persisted into the record an
             auditor reads. Config that changes no observable behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from veridian.core.report import (
    VerificationReport,
    append_report_jsonl,
    validate_report_chain,
)
from veridian.core.task import Task, TaskResult


def _report(task_id: str, passed: bool) -> VerificationReport:
    task = Task(id=task_id, title=f"t-{task_id}", verifier_id="schema")
    result = TaskResult(raw_output="x", structured={"ok": passed})
    return VerificationReport.from_task_result(
        task=task,
        result=result,
        passed=passed,
        error=None,
        evidence={},
        score=1.0 if passed else 0.0,
        runtime_version="test",
    )


def test_I1_1_tampered_audit_chain_must_be_detected() -> None:
    """PROPERTY (promised): an audit trail detects tampering by the holder.

    We act as the audited company: take a clean 3-link chain, flip a middle
    report from passed=True to passed=False (a compliance-relevant lie), then
    re-chain everything forward using ONLY Veridian's shipped public API.
    validate_report_chain MUST reject the forgery.

    This test asserts the security property. It FAILS today because the chain
    is unsigned: re-hashing makes the forgery indistinguishable from truth.
    """
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.jsonl"
        append_report_jsonl(path, _report("a", passed=False))  # real: task A FAILED
        append_report_jsonl(path, _report("b", passed=True))
        append_report_jsonl(path, _report("c", passed=True))

        # Sanity: honest chain validates.
        assert validate_report_chain(path).valid is True

        # --- FORGERY using only shipped public API ---
        lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        # The audited party rewrites history: task A "passed".
        lines[0]["passed"] = True
        lines[0]["score"] = 1.0

        prev_hash: str | None = None
        forged: list[dict] = []
        for raw in lines:
            rep = VerificationReport.from_dict(raw).with_previous_hash(prev_hash)
            forged.append(rep.to_dict())
            prev_hash = rep.report_hash
        path.write_text(
            "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in forged) + "\n"
        )

        validation = validate_report_chain(path)
        # The promise: forgery is caught. Reality: it is not.
        assert validation.valid is False, (
            "FORGED audit chain passed validation. An unsigned hash chain proves "
            "nothing against the party that holds the file — which is the only "
            "adversary an audit trail exists to catch."
        )


def test_I1_2_quickstart_produces_durable_evidence_chain_by_default() -> None:
    """PROPERTY (promised in README 'Why'): 'hashable verification reports for
    audit trails'. A default runner run should leave a durable, tamper-evident
    chain on disk. It does not — report_file defaults to None.
    """
    from veridian import MockProvider, TaskLedger, VeridianRunner
    from veridian.core.config import VeridianConfig

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=str(tmp_path / "progress.md"),
        )
        ledger = TaskLedger(cfg.ledger_file, progress_file=str(cfg.progress_file))
        ledger.add(
            [
                Task(
                    title="gate",
                    description="decide",
                    verifier_id="schema",
                    verifier_config={"schema": {"required": ["ok"]}},
                )
            ]
        )
        provider = MockProvider().script_veridian_result(structured={"ok": "yes"})
        VeridianRunner(ledger=ledger, provider=provider, config=cfg).run()

        evidence_files = list(tmp_path.glob("**/*.jsonl"))
        assert evidence_files, (
            "No durable evidence chain on disk after a default run. The headline "
            "'audit trail' is opt-in (report_file=None by default); the quickstart "
            "user gets nothing an auditor could receive."
        )


def test_I1_3_confidence_max_retries_changes_the_score() -> None:
    """PROPERTY (implied by the API): ConfidenceScore.compute takes max_retries,
    so max_retries should influence the score. It does not (`del max_retries`):
    a decorative parameter on a fabricated number that ships in the record.
    """
    from veridian.verify.builtin.confidence import ConfidenceScore

    tight = ConfidenceScore.compute(retry_count=2, max_retries=2)  # at the limit
    loose = ConfidenceScore.compute(retry_count=2, max_retries=100)  # far from limit

    assert tight.to_dict() != loose.to_dict(), (
        "max_retries changes nothing — being at the retry ceiling produces the "
        "exact same 'confidence' as being nowhere near it. The parameter is "
        "decorative; the number is theater."
    )
