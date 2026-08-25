from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_banking_agent_verification_showcase_runs_end_to_end() -> None:
    repository = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "examples/banking_agent_verification_demo.py"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["scenario"] == "offline-industrial-rtgs"
    assert result["action"] == {
        "amount": "USD 12,500,000.00",
        "amount_minor": 1_250_000_000,
        "payment_id": "PAY-2026-0009001",
        "rail": "RTGS",
        "transport": "openai.responses",
    }
    assert result["authorization"] == {
        "decision": "allow",
        "exact_semantic_binding": True,
        "policy_bound": True,
        "state_bound": True,
    }
    assert result["controls"] == {
        "agent_is_approver": False,
        "approval_quorum": "satisfied",
        "approver_count": 2,
        "approver_roles": ["checker", "maker"],
        "separation_of_duties": "satisfied",
    }
    assert result["mathematics"]["status"] == "satisfied"
    assert result["mathematics"]["repeatable"] is True
    assert set(result["mathematics"]["clauses"].values()) == {"satisfied"}
    assert result["execution"] == {
        "economic_effect_count": 1,
        "exact_retry_replayed": True,
        "first_call_replayed": False,
        "permit_max_uses": 1,
        "same_receipt_on_retry": True,
    }
    assert result["postconditions"] == {
        "completion_asserted": True,
        "effect_receipt_verified": True,
        "ledger_version_advanced": True,
        "settlement_satisfied": True,
        "settlement_status": "settled",
    }
    assert result["tamper"]["semantic_digest_changed"] is True
    assert result["tamper"]["gate_rejected"] is True
    assert result["tamper"]["executor_rejected"] is True
    assert result["tamper"]["economic_effect_count_after_attempt"] == 1
    assert "exact payment intent" in result["tamper"]["gate_reason"]
    assert "exact action" in result["tamper"]["executor_reason"]
