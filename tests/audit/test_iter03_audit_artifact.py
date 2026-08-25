"""
ADVERSARIAL AUDIT — Iteration 3: The artifact an auditor actually receives.

Iter-1 proved the chain is unsigned (forgeable). This iteration attacks a
different axis: even taking the hashes at face value, the evidence file cannot
answer the auditor's real question — "show me what the agent did, and prove this
record corresponds to it."

  I3-1 (P1): the JSONL evidence record contains only input_hash/output_hash, not
             the task input or agent output. validate_report_chain never
             recomputes those hashes (it can't — the data isn't there). The
             chain proves internal linkage, not correspondence to reality.
  I3-2 (P1): from_dict is lenient — arbitrary unhashed fields injected into an
             evidence line ride along and the chain still validates. Evidence
             pollution passes integrity checks.
  I3-3 (P2): an empty evidence file reports valid=True (vacuous attestation).
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

SIGNING_KEY = "audit-artifact-key-material-at-least-32"


def _report() -> VerificationReport:
    task = Task(
        id="t1", title="contract review", description="extract risk clause", verifier_id="schema"
    )
    result = TaskResult(raw_output="LLM said: risk is HIGH", structured={"risk": "HIGH"})
    return VerificationReport.from_task_result(
        task=task,
        result=result,
        passed=True,
        error=None,
        evidence={},
        score=1.0,
        runtime_version="test",
    )


def test_I3_1_evidence_record_redacts_output_but_keeps_commitment() -> None:
    """Default audit export protects raw output and retains a disclosure-time hash."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.jsonl"
        rep = _report()
        append_report_jsonl(path, rep, signing_key=SIGNING_KEY)

        line = json.loads(path.read_text().splitlines()[0])
        assert line["output_payload"] == {}
        assert line["result"] == {}
        assert line["output_hash"]


def test_I3_2_injected_unhashed_field_breaks_validation() -> None:
    """Integrity must cover the whole record. Inject an authoritative-looking
    field ('approved_by') into an evidence line; the chain must reject it.
    It does not: from_dict drops unknown keys before hashing, so the field
    rides along as apparent evidence inside a 'valid' chain.
    """
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.jsonl"
        append_report_jsonl(path, _report(), signing_key=SIGNING_KEY)

        line = json.loads(path.read_text().splitlines()[0])
        line["approved_by"] = "CFO"  # never hashed, never checked, looks official
        path.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")

        validation = validate_report_chain(path, signing_key=SIGNING_KEY)
        assert validation.valid is False, (
            "Injected unhashed field 'approved_by: CFO' passed chain validation. "
            "Anything outside the dataclass fields can be added to an evidence "
            "record after the fact and still validate — pollution masquerading "
            "as attested evidence."
        )


def test_I3_3_empty_evidence_file_is_not_a_valid_attestation() -> None:
    """A zero-record evidence file should not be reported as a valid attestation;
    'valid=True, checked_count=0' invites 'the chain validated' on an empty file.
    """
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "evidence.jsonl"
        path.write_text("")
        validation = validate_report_chain(path, signing_key=SIGNING_KEY)
        assert validation.valid is False, (
            "Empty evidence file reports valid=True. 'The audit chain validated' "
            "is technically true and substantively a lie when there is nothing in it."
        )
