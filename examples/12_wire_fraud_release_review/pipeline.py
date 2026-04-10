"""
Problem 12: Wire Fraud Release Review
=====================================
Mission-critical control for high-value wire release in BEC-prone workflows.

What this demo proves:
  1. Deterministic pre-screening computes risk before any model call.
  2. High-risk wires pause for dual approval (HITL) and survive restart.
  3. Sanctioned wires are blocked and never released.
  4. Release side effects are wrapped in run_activity(), so replay does not
     duplicate transfer calls.

Run:
    python examples/12_wire_fraud_release_review/pipeline.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

# Repo root on sys.path so this script is runnable from any working directory.
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from veridian.core.config import VeridianConfig
from veridian.core.exceptions import TaskPauseRequested
from veridian.core.task import Task, TaskStatus
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.activity import ActivityJournal, RetryPolicy, run_activity
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.providers.mock_provider import MockProvider

_EXAMPLE_DIR = Path(__file__).parent
_PAYMENT_FILES = (
    _EXAMPLE_DIR / "normal_payment.json",
    _EXAMPLE_DIR / "suspicious_payment.json",
    _EXAMPLE_DIR / "sanctioned_beneficiary.json",
)

_WIRE_VERIFIER_CONFIG: dict[str, Any] = {
    "verifiers": [
        {
            "id": "schema",
            "config": {
                "required_fields": [
                    "decision",
                    "risk_score",
                    "sanctions_match",
                    "beneficiary_verified",
                    "dual_approval_required",
                    "policy_reference",
                    "release_comment",
                ]
            },
        },
        {
            "id": "semantic_grounding",
            "config": {
                "consistency_rules": [
                    {
                        "if_field": "decision",
                        "equals": "ALLOW",
                        "then_field": "sanctions_match",
                        "must_equal": False,
                    },
                    {
                        "if_field": "decision",
                        "equals": "ALLOW",
                        "then_field": "beneficiary_verified",
                        "must_equal": True,
                    },
                    {
                        "if_field": "sanctions_match",
                        "equals": True,
                        "then_field": "decision",
                        "must_equal": "BLOCK",
                    },
                ],
                "range_checks": [{"field": "risk_score", "min": 0, "max": 100}],
                "check_artifacts_match_summary": False,
            },
        },
    ]
}


@dataclass(frozen=True)
class RiskAssessment:
    risk_score: int
    requires_dual_approval: bool
    hard_block: bool
    indicators: list[str]


class DualApprovalHook(BaseHook):
    """Pause high-risk payment tasks until 2 independent approvals are present."""

    id: ClassVar[str] = "dual_approval_gate"
    priority: ClassVar[int] = 10

    def __init__(self, required_approvals: int = 2) -> None:
        self.required_approvals = required_approvals

    def before_task(self, event: Any) -> None:
        task = getattr(event, "task", None)
        if task is None or not task.metadata.get("requires_dual_approval", False):
            return

        approvals = task.metadata.get("approvals", [])
        approved_count = len([a for a in approvals if isinstance(a, str) and a.strip()])
        if approved_count >= self.required_approvals:
            return

        raise TaskPauseRequested(
            task_id=task.id,
            reason="Dual approval required before release",
            payload={
                "cursor": {"stage": "dual_approval_gate"},
                "payment_id": task.metadata.get("payment_id"),
                "required_approvals": self.required_approvals,
                "received_approvals": approved_count,
                "risk_score": task.metadata.get("risk_score"),
                "resume_hint": "Add two independent approvers in metadata.approvals",
            },
        )


class WireGateway:
    """Deterministic transfer gateway used by the release activity boundary."""

    def __init__(self) -> None:
        self.calls = 0

    def release_wire(
        self,
        payment_id: str,
        amount_usd: float,
        beneficiary_account: str,
    ) -> dict[str, Any]:
        self.calls += 1
        return {
            "transfer_id": f"TRX-{payment_id}",
            "amount_usd": amount_usd,
            "beneficiary_account": beneficiary_account,
            "settlement_state": "settled",
        }


def load_payment_cases() -> list[dict[str, Any]]:
    """Load all JSON payment cases for this example."""
    cases: list[dict[str, Any]] = []
    for path in _PAYMENT_FILES:
        cases.append(json.loads(path.read_text(encoding="utf-8")))
    return cases


def assess_payment_risk(case: dict[str, Any]) -> RiskAssessment:
    """Deterministic pre-screening prior to model execution."""
    indicators: list[str] = []
    score = 0

    if bool(case.get("sanctions_match", False)):
        return RiskAssessment(
            risk_score=100,
            requires_dual_approval=False,
            hard_block=True,
            indicators=["sanctions_match"],
        )

    if float(case.get("amount_usd", 0.0)) >= 200_000:
        score += 35
        indicators.append("high_amount")
    if bool(case.get("is_new_beneficiary", False)):
        score += 25
        indicators.append("new_beneficiary")
    if not bool(case.get("callback_verified", True)):
        score += 20
        indicators.append("callback_not_verified")
    if bool(case.get("email_domain_mismatch", False)):
        score += 15
        indicators.append("email_domain_mismatch")
    if bool(case.get("urgency_language", False)):
        score += 10
        indicators.append("urgency_language")
    if str(case.get("originator_country")) != str(case.get("beneficiary_country")):
        score += 10
        indicators.append("cross_border_payment")

    requires_dual_approval = score >= 60
    return RiskAssessment(
        risk_score=min(score, 99),
        requires_dual_approval=requires_dual_approval,
        hard_block=False,
        indicators=indicators,
    )


def build_task(case: dict[str, Any]) -> Task:
    """Convert a payment case into a Veridian task."""
    assessment = assess_payment_risk(case)
    payment_id = str(case["payment_id"])
    amount_usd = float(case["amount_usd"])

    description = (
        f"Review wire payment {payment_id} for fraud-safe release.\n"
        f"Originator: {case['originator']} ({case['originator_country']})\n"
        f"Beneficiary: {case['beneficiary_name']} ({case['beneficiary_country']})\n"
        f"Beneficiary account: {case['beneficiary_account']}\n"
        f"Amount: ${amount_usd:,.2f}\n\n"
        "Produce a <veridian:result> JSON payload with structured fields:\n"
        "decision: ALLOW | BLOCK\n"
        "risk_score: integer 0-100\n"
        "sanctions_match: boolean\n"
        "beneficiary_verified: boolean\n"
        "dual_approval_required: boolean\n"
        "policy_reference: short policy id\n"
        "release_comment: one-sentence rationale\n"
    )

    if assessment.requires_dual_approval:
        priority = 100
    elif assessment.hard_block:
        priority = 90
    else:
        priority = 50

    return Task(
        id=case["id"],
        title=f"Wire Release Review: {payment_id}",
        description=description,
        verifier_id="composite",
        verifier_config=_WIRE_VERIFIER_CONFIG,
        priority=priority,
        phase="wire_release_review",
        metadata={
            **case,
            "risk_score": assessment.risk_score,
            "risk_indicators": assessment.indicators,
            "requires_dual_approval": assessment.requires_dual_approval,
            "hard_block": assessment.hard_block,
            "approvals": [],
        },
    )


def script_worker_outputs(provider: MockProvider, cases: list[dict[str, Any]]) -> None:
    """
    Script deterministic model outputs in runtime execution order.

    Tasks requiring dual approval pause before worker execution. Those responses
    are intentionally queued after non-paused tasks so a run1/run2 split still
    maps responses to the right task.
    """
    ordered_cases: list[dict[str, Any]] = sorted(
        cases,
        key=lambda c: (
            1 if assess_payment_risk(c).requires_dual_approval else 0,
            -assess_payment_risk(c).risk_score,
            c["payment_id"],
        ),
    )

    for case in ordered_cases:
        assessment = assess_payment_risk(case)
        sanctions_match = bool(case.get("sanctions_match", False))
        expected = str(case.get("expected_outcome", "allow")).lower()

        if expected == "block":
            provider.script_veridian_result(
                {
                    "decision": "BLOCK",
                    "risk_score": 100,
                    "sanctions_match": True,
                    "beneficiary_verified": False,
                    "dual_approval_required": False,
                    "policy_reference": "WIRE-SANCTIONS-001",
                    "release_comment": "Sanctions screening matched; payment blocked.",
                },
                summary="Payment blocked due to sanctions match.",
            )
            continue

        provider.script_veridian_result(
            {
                "decision": "ALLOW",
                "risk_score": assessment.risk_score,
                "sanctions_match": sanctions_match,
                "beneficiary_verified": True,
                "dual_approval_required": assessment.requires_dual_approval,
                "policy_reference": "WIRE-DUAL-APPROVAL-002",
                "release_comment": "Release allowed after deterministic checks and controls.",
            },
            summary="Payment approved for release.",
        )


def grant_dual_approval(ledger: TaskLedger, task_id: str, approvers: list[str]) -> None:
    """Persist approvals in task metadata so paused tasks can resume."""
    task = ledger.get(task_id)
    unique = sorted({a.strip() for a in approvers if a.strip()})
    task.metadata["approvals"] = unique
    ledger.add([task], skip_duplicates=False)


def release_approved_wires(ledger: TaskLedger, gateway: WireGateway) -> dict[str, str]:
    """
    Execute release side effects through run_activity().

    The release journal is stored in task.result.extras and reloaded on replay,
    ensuring we never duplicate transfer calls.
    """
    statuses: dict[str, str] = {}
    for task in ledger.list(status=TaskStatus.DONE, phase="wire_release_review"):
        result = task.result
        if result is None:
            continue

        decision = str(result.structured.get("decision", "")).upper()
        if decision == "BLOCK":
            result.extras["release_status"] = "blocked"
            ledger.checkpoint_result(task.id, result)
            statuses[task.id] = "blocked"
            continue

        if decision != "ALLOW":
            result.extras["release_status"] = "not_released"
            ledger.checkpoint_result(task.id, result)
            statuses[task.id] = "not_released"
            continue

        journal_data = result.extras.get("release_activity_journal")
        journal = (
            ActivityJournal.from_list(journal_data)
            if isinstance(journal_data, list)
            else ActivityJournal()
        )

        activity_id = f"wire_release:{task.id}:{task.metadata['payment_id']}"
        receipt = run_activity(
            journal=journal,
            fn=gateway.release_wire,
            args=(
                str(task.metadata["payment_id"]),
                float(task.metadata["amount_usd"]),
                str(task.metadata["beneficiary_account"]),
            ),
            fn_name="wire_gateway.release_wire",
            idempotency_key=activity_id,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
        )

        result.extras["release_status"] = "released"
        result.extras["release_receipt"] = receipt
        result.extras["release_activity_journal"] = journal.to_list()
        ledger.checkpoint_result(task.id, result)
        statuses[task.id] = "released"
    return statuses


def _print_summary(
    cases: list[dict[str, Any]],
    first: RunSummary,
    second: RunSummary,
    ledger: TaskLedger,
    gateway: WireGateway,
) -> None:
    print("\n" + "=" * 78)
    print("WIRE FRAUD RELEASE REVIEW")
    print("=" * 78)
    print(
        f"Run 1: done={first.done_count}, failed={first.failed_count}, "
        f"abandoned={first.abandoned_count}"
    )
    print(
        f"Run 2: done={second.done_count}, failed={second.failed_count}, "
        f"abandoned={second.abandoned_count}"
    )
    print(f"Gateway calls (after replay-safe release pass): {gateway.calls}")
    print("-" * 78)
    print(f"{'Payment':<18} {'Task Status':<12} {'Decision':<8} {'Release':<10} {'Risk':<4}")
    print("-" * 78)
    by_id = {c["id"]: c for c in cases}
    for task in ledger.list(phase="wire_release_review"):
        result = task.result or None
        decision = str(result.structured.get("decision", "")) if result else ""
        release_status = str(result.extras.get("release_status", "")) if result else ""
        risk = str(task.metadata.get("risk_score", ""))
        payment_id = str(by_id.get(task.id, {}).get("payment_id", task.id))
        print(
            f"{payment_id:<18} {task.status.value:<12} {decision:<8} {release_status:<10} {risk:<4}"
        )


def main() -> None:
    cases = load_payment_cases()

    with tempfile.TemporaryDirectory(prefix="veridian_wire_release_") as tmp:
        tmp_path = Path(tmp)
        ledger_path = tmp_path / "ledger.json"
        progress_path = tmp_path / "progress.md"

        ledger = TaskLedger(path=ledger_path, progress_file=str(progress_path))
        ledger.add([build_task(case) for case in cases])

        provider = MockProvider()
        script_worker_outputs(provider, cases)

        hooks = HookRegistry()
        hooks.register(DualApprovalHook(required_approvals=2))

        config = VeridianConfig(
            max_turns_per_task=1,
            max_retries=1,
            ledger_file=ledger_path,
            progress_file=progress_path,
            activity_journal_enabled=True,
            resume_paused_on_start=True,
        )
        runner = VeridianRunner(ledger=ledger, provider=provider, config=config, hooks=hooks)

        first_summary = runner.run()

        for paused in ledger.list(status=TaskStatus.PAUSED, phase="wire_release_review"):
            grant_dual_approval(
                ledger=ledger,
                task_id=paused.id,
                approvers=["ops_manager", "risk_officer"],
            )

        second_summary = runner.run()

        gateway = WireGateway()
        release_approved_wires(ledger, gateway)

        # Replay pass on a fresh ledger instance should not duplicate release.
        replay_ledger = TaskLedger(path=ledger_path, progress_file=str(progress_path))
        release_approved_wires(replay_ledger, gateway)

        _print_summary(cases, first_summary, second_summary, replay_ledger, gateway)


if __name__ == "__main__":
    main()
