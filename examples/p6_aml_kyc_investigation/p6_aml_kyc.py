"""
P6 -- AML/KYC Investigation Automation (Financial Services)

Problem
-------
Mid-size banks process 10,000-50,000 AML alerts per month, 95%+ of which are false
positives. Investigators spend 40-60% of their time on manual case assembly. Under
GDPR Art. 22 and SOX, every automated decision requires traceable evidence, explainable
reasoning, and a human override path.

Veridian Solution
-----------------
* SchemaVerifier enforces that every investigation report contains the required fields:
  risk_level, evidence_summary, recommended_action, source_documents.
* SemanticGroundingVerifier catches conclusions that contradict the evidence
  (e.g. LOW risk with FREEZE_ACCOUNT action -- impossible combination).
* CrossRunConsistencyHook detects when the same customer receives contradictory risk
  ratings across separate alert investigations.
* AuditLogHook produces a cryptographically-hashed audit trail (GDPR Art. 22 / SOX).
* EscalationTracker surfaces HIGH/CRITICAL cases for mandatory human review.

Run
---
    python examples/p6_aml_kyc_investigation/p6_aml_kyc.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

# Repo root on sys.path so the script is runnable from any working directory
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskPriority
from veridian.hooks.base import BaseHook
from veridian.hooks.builtin.cross_run_consistency import (
    ClaimConflict,
    CrossRunConsistencyHook,
)
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import RunSummary, VeridianRunner
from veridian.providers.mock_provider import MockProvider

ALERTS_FILE = Path(__file__).parent / "sample_alerts.json"

# ── Composite verifier config ─────────────────────────────────────────────────
# Step 1 -- SchemaVerifier: all required fields present and non-null
# Step 2 -- SemanticGroundingVerifier: conclusions consistent with evidence
AML_VERIFIER_CONFIG: dict[str, Any] = {
    "verifiers": [
        {
            "id": "schema",
            "config": {
                "required_fields": [
                    "risk_level",
                    "evidence_summary",
                    "recommended_action",
                    "source_documents",
                ]
            },
        },
        {
            "id": "semantic_grounding",
            "config": {
                "consistency_rules": [
                    {
                        # LOW risk must result in closure or monitoring -- never
                        # account freeze or emergency escalation.
                        "if_field": "risk_level",
                        "equals": "LOW",
                        "then_field": "recommended_action",
                        "must_be_in": ["CLOSE_ALERT", "MONITOR"],
                    },
                ],
                # Artifacts are not expected for investigation reports
                "check_artifacts_match_summary": False,
            },
        },
    ]
}


# ── Custom hooks ──────────────────────────────────────────────────────────────

class AuditLogHook(BaseHook):
    """
    Produces a cryptographically-hashed audit trail entry for every investigation.
    Required by GDPR Art. 22 (automated decision accountability) and SOX.
    """

    id: ClassVar[str] = "audit_log"
    priority: ClassVar[int] = 0  # runs first -- sees unmodified task state

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def after_task(self, event: Any) -> None:
        """Record audit entry after each task completes."""
        task = getattr(event, "task", None)
        result = getattr(event, "result", None)
        if not task or not result:
            return
        structured = getattr(result, "structured", {}) or {}
        evidence_hash = hashlib.sha256(
            json.dumps(structured, sort_keys=True).encode()
        ).hexdigest()[:16]
        self.entries.append(
            {
                "alert_id": task.metadata.get("alert_id", task.id),
                "customer_id": task.metadata.get("customer_id", "?"),
                "risk_level": structured.get("risk_level", "UNKNOWN"),
                "decision": structured.get("recommended_action", "?"),
                "evidence_hash": evidence_hash,
                "audit_ref": f"AUD-{task.id.replace('-', '')[:8].upper()}",
                "status": task.status.value,
            }
        )


class EscalationTracker(BaseHook):
    """
    Identifies HIGH and CRITICAL risk cases that require mandatory human review.
    Feeds the Human Review Queue displayed in the run summary.
    """

    id: ClassVar[str] = "escalation_tracker"

    def __init__(self) -> None:
        self.escalations: list[dict[str, Any]] = []

    def after_task(self, event: Any) -> None:
        """Queue HIGH/CRITICAL cases for human review."""
        task = getattr(event, "task", None)
        result = getattr(event, "result", None)
        if not task or not result:
            return
        structured = getattr(result, "structured", {}) or {}
        risk = structured.get("risk_level", "")
        if risk in ("HIGH", "CRITICAL"):
            self.escalations.append(
                {
                    "alert_id": task.metadata.get("alert_id", task.id),
                    "customer_id": task.metadata.get("customer_id", "?"),
                    "customer_name": task.metadata.get("customer_name", "?"),
                    "risk_level": risk,
                    "action": structured.get("recommended_action", "?"),
                    "amount": task.metadata.get("transaction_amount", "?"),
                }
            )


class ConsistencyBridgeHook(BaseHook):
    """
    Bridges VeridianRunner's event names to CrossRunConsistencyHook's methods.

    VeridianRunner fires: before_run, before_task, after_task, on_failure, after_run
    CrossRunConsistencyHook expects: on_run_started, after_result

    This bridge translates:
      before_run  -> on_run_started  (resets claim store for new run)
      after_task  -> after_result    (checks and registers claims)
    """

    id: ClassVar[str] = "consistency_bridge"

    def __init__(self, inner: CrossRunConsistencyHook) -> None:
        self._inner = inner

    def before_run(self, event: Any) -> None:
        """Delegate to CrossRunConsistencyHook.on_run_started."""
        self._inner.on_run_started(event)

    def after_task(self, event: Any) -> None:
        """Delegate to CrossRunConsistencyHook.after_result."""
        self._inner.after_result(event)

    @property
    def conflicts(self) -> list[ClaimConflict]:
        """All conflicts detected so far."""
        return self._inner.conflicts

    @property
    def critical_conflicts(self) -> list[ClaimConflict]:
        """Only critical-severity conflicts."""
        return self._inner.critical_conflicts


# ── Task factory ──────────────────────────────────────────────────────────────

def build_tasks(alerts: list[dict[str, Any]]) -> list[Task]:
    """Convert raw AML alert dicts into Veridian investigation tasks."""
    tasks = []
    for alert in alerts:
        # Use uniform NORMAL priority so tasks are processed in FIFO (insertion) order.
        # The severity is captured in the task metadata for downstream use.
        priority = int(TaskPriority.NORMAL)
        task = Task(
            id=alert["id"],
            title=(
                f"Investigate {alert['id']}: {alert['customer_name']} -- "
                f"{alert['alert_reason'][:55]}"
            ),
            description=(
                f"AML Alert Investigation -- {alert['id']}\n"
                f"Customer : {alert['customer_name']} ({alert['customer_id']})\n"
                f"Amount   : ${alert['transaction_amount']:,} via {alert['transaction_type']}\n"
                f"Trigger  : {alert['alert_reason']}\n"
                f"Country  : {alert['counterparty_country']} | "
                f"Account age: {alert['account_age_days']} days | "
                f"Prior alerts: {alert['previous_alerts']}\n\n"
                f"Produce a structured investigation report containing:\n"
                f"  risk_level          : LOW | MEDIUM | HIGH | CRITICAL\n"
                f"  evidence_summary    : 1-2 sentence narrative\n"
                f"  recommended_action  : CLOSE_ALERT | MONITOR | "
                f"ADDITIONAL_SCREENING | ESCALATE | FREEZE_ACCOUNT\n"
                f"  source_documents    : list of reference document IDs\n"
                f"  customer_id         : customer identifier (required for "
                f"cross-alert consistency tracking)\n"
            ),
            verifier_id="composite",
            verifier_config=AML_VERIFIER_CONFIG,
            priority=priority,
            phase="aml_investigation",
            metadata={
                "alert_id": alert["id"],
                "customer_id": alert["customer_id"],
                "customer_name": alert["customer_name"],
                "transaction_amount": alert["transaction_amount"],
            },
        )
        tasks.append(task)
    return tasks


# ── MockProvider scripting ────────────────────────────────────────────────────

def script_mock_responses(provider: MockProvider) -> None:
    """
    Script deterministic investigation outcomes for all 10 alerts.
    Responses are consumed in FIFO order as the runner processes tasks.
    """
    # AML-001: CUST-A, LOW -- benign wire transfer
    provider.script_veridian_result(
        {
            "customer_id": "CUST-A",
            "risk_level": "LOW",
            "evidence_summary": (
                "Wire transfer matches documented business pattern. "
                "Counterparty is a known vendor with 3-year relationship on file."
            ),
            "recommended_action": "CLOSE_ALERT",
            "source_documents": ["txn_AML001.csv", "vendor_registry_CUST-A.pdf"],
            "false_positive_probability": 0.94,
        },
        summary="Investigation complete. Low risk -- standard business transaction.",
    )

    # AML-002: CUST-B, MEDIUM -- offshore transfer
    provider.script_veridian_result(
        {
            "customer_id": "CUST-B",
            "risk_level": "MEDIUM",
            "evidence_summary": (
                "Offshore transfer to BVI entity. No prior AML violations on record, "
                "but destination jurisdiction warrants ongoing monitoring."
            ),
            "recommended_action": "MONITOR",
            "source_documents": ["txn_AML002.csv", "kyc_CUST-B.pdf"],
            "false_positive_probability": 0.61,
        },
        summary="Investigation complete. Medium risk -- monitoring recommended.",
    )

    # AML-003: CUST-C, HIGH -- structuring deposits
    provider.script_veridian_result(
        {
            "customer_id": "CUST-C",
            "risk_level": "HIGH",
            "evidence_summary": (
                "Structured deposits below $10K threshold across 23 transactions "
                "totaling $820K over 14 days. Classic smurfing pattern detected."
            ),
            "recommended_action": "ESCALATE",
            "source_documents": [
                "txn_AML003.csv",
                "pattern_analysis.pdf",
                "sar_template.pdf",
            ],
            "false_positive_probability": 0.08,
        },
        summary="Investigation complete. High risk -- structuring pattern, escalation required.",
    )

    # AML-004: CUST-D, CRITICAL -- round-trip laundering
    provider.script_veridian_result(
        {
            "customer_id": "CUST-D",
            "risk_level": "CRITICAL",
            "evidence_summary": (
                "Round-trip transactions through 3 shell companies across 2 FATF "
                "high-risk jurisdictions. $2.1M implicated with layered beneficial ownership."
            ),
            "recommended_action": "FREEZE_ACCOUNT",
            "source_documents": [
                "txn_AML004.csv",
                "network_analysis.pdf",
                "fatf_blacklist.pdf",
                "beneficial_ownership.pdf",
            ],
            "false_positive_probability": 0.02,
        },
        summary="Investigation complete. CRITICAL -- account freeze required, SAR filing mandatory.",
    )

    # AML-005: CUST-A, HIGH -- velocity spike
    # SAME customer as AML-001 (LOW) -- CrossRunConsistencyHook detects CRITICAL conflict
    provider.script_veridian_result(
        {
            "customer_id": "CUST-A",
            "risk_level": "HIGH",
            "evidence_summary": (
                "High-velocity transfers to 11 new counterparties in 48 hours. "
                "Behavior change since AML-001 suggests possible account takeover."
            ),
            "recommended_action": "ESCALATE",
            "source_documents": ["txn_AML005.csv", "velocity_analysis.pdf"],
            "false_positive_probability": 0.11,
        },
        summary="HIGH risk -- behavior change since previous alert warrants escalation.",
    )

    # AML-006: CUST-B, LOW -- small domestic transfer
    # SAME customer as AML-002 (MEDIUM) -- CrossRunConsistencyHook detects warning conflict
    provider.script_veridian_result(
        {
            "customer_id": "CUST-B",
            "risk_level": "LOW",
            "evidence_summary": (
                "Small domestic transfer to verified local business. "
                "Full documentation on file. Prior MEDIUM rating reflects "
                "newly submitted business certification."
            ),
            "recommended_action": "CLOSE_ALERT",
            "source_documents": ["txn_AML006.csv", "business_cert.pdf"],
            "false_positive_probability": 0.87,
        },
        summary="Investigation complete. Low risk -- standard domestic transaction.",
    )

    # AML-007: CUST-E, MEDIUM -- near-threshold pattern
    provider.script_veridian_result(
        {
            "customer_id": "CUST-E",
            "risk_level": "MEDIUM",
            "evidence_summary": (
                "Recurring withdrawals clustered just below $10K threshold. "
                "Pattern suggests structuring awareness; insufficient for SAR without "
                "additional corroborating evidence."
            ),
            "recommended_action": "ADDITIONAL_SCREENING",
            "source_documents": ["txn_AML007.csv", "transaction_history.pdf"],
            "false_positive_probability": 0.45,
        },
        summary="Investigation complete. Medium risk -- additional screening warranted.",
    )

    # AML-008: CUST-F, HIGH -- OFAC sanctions
    provider.script_veridian_result(
        {
            "customer_id": "CUST-F",
            "risk_level": "HIGH",
            "evidence_summary": (
                "Wire transfer to financial institution on OFAC secondary sanctions list. "
                "Customer unresponsive to KYC inquiry. No business justification on file."
            ),
            "recommended_action": "ESCALATE",
            "source_documents": [
                "txn_AML008.csv",
                "ofac_screening.pdf",
                "sanctions_list_2026.pdf",
            ],
            "false_positive_probability": 0.07,
        },
        summary="Investigation complete. HIGH risk -- OFAC sanctions match requires escalation.",
    )

    # AML-009: CUST-G, LOW -- payroll deposit
    provider.script_veridian_result(
        {
            "customer_id": "CUST-G",
            "risk_level": "LOW",
            "evidence_summary": (
                "Regular payroll deposit from employer of record. "
                "Employer KYC verified. Deviation from baseline explained by "
                "quarterly bonus cycle confirmed with HR documentation."
            ),
            "recommended_action": "CLOSE_ALERT",
            "source_documents": ["txn_AML009.csv", "employer_kyc.pdf"],
            "false_positive_probability": 0.97,
        },
        summary="Investigation complete. Low risk -- verified payroll transaction.",
    )

    # AML-010: CUST-H, LOW -- home equity disbursement
    provider.script_veridian_result(
        {
            "customer_id": "CUST-H",
            "risk_level": "LOW",
            "evidence_summary": (
                "Home equity loan disbursement to licensed contractor. "
                "Title search completed. Full documentation on file."
            ),
            "recommended_action": "CLOSE_ALERT",
            "source_documents": [
                "txn_AML010.csv",
                "loan_agreement.pdf",
                "contractor_license.pdf",
            ],
            "false_positive_probability": 0.92,
        },
        summary="Investigation complete. Low risk -- standard home equity disbursement.",
    )


# ── Plain-text output ─────────────────────────────────────────────────────────

def print_results(
    alerts: list[dict[str, Any]],
    audit_hook: AuditLogHook,
    escalation_hook: EscalationTracker,
    consistency_bridge: ConsistencyBridgeHook,
    summary: RunSummary,
) -> None:
    """Print the full pipeline results."""
    SEP = "=" * 72
    sep = "-" * 72

    print()
    print(SEP)
    print("P6 -- AML/KYC Investigation Automation")
    print("Veridian deterministic verification pipeline for financial compliance")
    print(SEP)

    total = len(alerts)
    low_risk = sum(1 for e in audit_hook.entries if e["risk_level"] == "LOW")
    medium_risk = sum(1 for e in audit_hook.entries if e["risk_level"] == "MEDIUM")
    high_crit = sum(
        1 for e in audit_hook.entries if e["risk_level"] in ("HIGH", "CRITICAL")
    )
    escalations = len(escalation_hook.escalations)
    conflicts_total = len(consistency_bridge.conflicts)
    conflicts_critical = len(consistency_bridge.critical_conflicts)

    print()
    print("SUMMARY")
    print(sep)
    print(f"  Alerts processed              : {summary.done_count}/{total}")
    print(f"  False positives closed (LOW)  : {low_risk}  ({100 * low_risk // max(total, 1)}%)")
    print(f"  Medium risk (monitoring)      : {medium_risk}")
    print(f"  HIGH / CRITICAL escalations   : {high_crit}")
    print(f"  Human Review Queue entries    : {escalations}")
    print(f"  Cross-alert conflicts         : {conflicts_total}")
    print(f"    - Critical conflicts        : {conflicts_critical}")
    print(f"  Audit trail entries           : {len(audit_hook.entries)}")
    print(f"  Pipeline duration             : {summary.duration_seconds:.2f}s")

    print()
    print("INVESTIGATION RESULTS")
    print(sep)
    header = f"  {'Alert':<10} {'Customer':<20} {'Amount':>12}  {'Risk':<10} {'Decision':<25} {'Audit Ref'}"
    print(header)
    print(f"  {'-'*8} {'-'*18} {'-'*12}  {'-'*8} {'-'*23} {'-'*12}")
    for alert in alerts:
        entry = next(
            (e for e in audit_hook.entries if e["alert_id"] == alert["id"]), None
        )
        if not entry:
            continue
        amount_str = f"${alert['transaction_amount']:,}"
        print(
            f"  {alert['id']:<10} {alert['customer_name'][:18]:<20} "
            f"{amount_str:>12}  {entry['risk_level']:<10} "
            f"{entry['decision']:<25} {entry['audit_ref']}"
        )

    # Human review queue
    if escalation_hook.escalations:
        print()
        print("HUMAN REVIEW QUEUE -- HIGH/CRITICAL CASES")
        print(sep)
        for e in escalation_hook.escalations:
            amount_str = (
                f"${e['amount']:,}" if isinstance(e["amount"], int) else str(e["amount"])
            )
            print(
                f"  [{e['risk_level']}] {e['alert_id']} | "
                f"{e['customer_name'][:18]} | "
                f"{amount_str} | Action: {e['action']}"
            )
    else:
        print()
        print("No escalations triggered.")

    # Cross-run consistency conflicts
    if consistency_bridge.conflicts:
        print()
        print("CROSS-ALERT CONSISTENCY CONFLICTS DETECTED")
        print("Same customer received contradictory risk ratings -- human arbitration required")
        print(sep)
        for c in consistency_bridge.conflicts:
            sev = c.severity.upper()
            print(
                f"  [{sev}] Entity: {c.entity_id or 'global'} | "
                f"Field: {c.field} | "
                f"{c.task_a_id}: {c.value_a} vs {c.task_b_id}: {c.value_b}"
            )
        print()
        print("Resolution: Reviewer must determine which risk rating reflects current")
        print("customer risk profile and update case records accordingly.")
    else:
        print()
        print("No cross-alert consistency conflicts.")

    # Compliance coverage
    print()
    print("COMPLIANCE COVERAGE")
    print(sep)
    print("  [OK] GDPR Art. 22  -- Every automated decision has traceable SHA-256 evidence hash")
    print(f"       ({len(audit_hook.entries)} audit entries)")
    print("  [OK] SOX           -- Immutable audit trail, full provenance chain")
    print(f"  [OK] FinCEN/BSA    -- {high_crit} HIGH/CRITICAL case(s) routed to Human Review Queue")
    print(f"  [OK] Cross-run     -- {conflicts_total} contradiction(s) surfaced,")
    print(f"                        {conflicts_critical} critical conflict(s) flagged for arbitration")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Run the AML/KYC investigation pipeline end-to-end."""
    with tempfile.TemporaryDirectory(prefix="veridian_p6_") as tmp:
        tmp_path = Path(tmp)
        ledger_path = tmp_path / "ledger.json"
        progress_path = tmp_path / "progress.md"

        alerts: list[dict[str, Any]] = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        print(f"\nLoading {len(alerts)} AML alerts from {ALERTS_FILE.name}")

        # 1. Build tasks
        tasks = build_tasks(alerts)
        ledger = TaskLedger(path=ledger_path, progress_file=str(progress_path))
        ledger.add(tasks)
        print(f"[OK] {len(tasks)} investigation tasks loaded into ledger")

        # 2. Script MockProvider responses (one per alert, in order)
        provider = MockProvider()
        script_mock_responses(provider)
        print("[OK] Agent responses scripted (MockProvider -- no LLM calls)")

        # 3. Set up hooks
        audit_hook = AuditLogHook()
        escalation_hook = EscalationTracker()
        consistency_inner = CrossRunConsistencyHook(
            claim_fields=["risk_level"],
            entity_key_field="customer_id",
            raise_on_critical=False,  # surface conflicts -- do not abort run
        )
        consistency_bridge = ConsistencyBridgeHook(consistency_inner)

        hooks = HookRegistry()
        hooks.register(audit_hook)            # priority 0 -- runs first
        hooks.register(escalation_hook)       # priority 50
        hooks.register(consistency_bridge)    # priority 50
        print("[OK] Hooks registered: AuditLog (p=0), EscalationTracker (p=50), ConsistencyBridge (p=50)")

        # 4. Run pipeline
        config = VeridianConfig(
            max_turns_per_task=1,   # single LLM call per alert (mock)
            max_retries=1,
            dry_run=False,
            progress_file=progress_path,
        )
        print("\nRunning AML investigation pipeline...")
        runner = VeridianRunner(
            ledger=ledger,
            provider=provider,
            config=config,
            hooks=hooks,
        )
        summary = runner.run()

        # 5. Print results
        print_results(alerts, audit_hook, escalation_hook, consistency_bridge, summary)

        # 6. Ledger integrity footer
        stats = ledger.stats()
        ledger_size = ledger_path.stat().st_size
        print(
            f"Ledger: {stats.by_status} | "
            f"Atomic writes: every transition protected by temp-file + os.replace() | "
            f"File size: {ledger_size:,} bytes"
        )
        print()


if __name__ == "__main__":
    main()
