"""
Problem 1: AI Code Deploys Without Verification
=================================================
Preventing the Amazon March 2026 outage pattern.

INCIDENT (Amazon, March 2026):
  Mar 2: 6hr disruption, 120K lost orders, 1.6M website errors.
  Mar 5: 6hr outage, 99% drop in US order volume, ~6.3M lost orders.
  Cause: Amazon Kiro agent autonomously deployed code changes.
  Internal memo: "trend of incidents" with "high blast radius."
  Fix: Amazon now requires senior sign-offs for ALL AI-assisted code.

INDUSTRY DATA:
  AI code: 1.7x more bugs, 1.5-2x more security vulnerabilities.
  Incidents per PR: +23.5% with AI assistance.

VERIDIAN TWO-GATE VERIFICATION:
  Gate 1: ToolSafetyVerifier (AST) — blocks unsafe code patterns
  Gate 2: SchemaVerifier — ensures output has required fields
  Both deterministic. LLM cannot override either.

USAGE:
    pip install veridian-ai
    python solution.py
"""

from __future__ import annotations

import time

from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.schema import SchemaVerifier
from veridian.verify.builtin.tool_safety import ToolSafetyVerifier

_safety = ToolSafetyVerifier()
_schema = SchemaVerifier(required_fields=["status", "migration_complete"])


def verify_deploy(code: str, output: dict[str, object], label: str = "d") -> tuple[str, str]:
    """Two-gate: AST safety + schema validation. Both must pass."""
    task = Task(id=label, title="deploy", verifier_id="composite")
    g1 = _safety.verify(task, TaskResult(raw_output=code, structured={"code": code}))
    if not g1.passed:
        return "BLOCKED", f"Gate 1 (AST): {g1.error}"
    g2 = _schema.verify(task, TaskResult(raw_output="", structured=output))
    if not g2.passed:
        return "BLOCKED", f"Gate 2 (Schema): {g2.error}"
    return "DEPLOY", "Both gates passed"


SCENARIOS = [
    (
        "kiro_os_system",
        "import os\nos.system('kubectl apply -f deploy.yaml --force')",
        {"status": "deployed", "migration_complete": True},
        "Amazon Kiro pattern: os.system() deploying configs",
    ),
    (
        "env_credential_leak",
        "import os\nurl = os.environ['PROD_DATABASE_URL']",
        {"status": "ok", "migration_complete": True},
        "Credential leak: os.environ reading production secrets",
    ),
    (
        "eval_config",
        "config = eval(open('deploy.py').read())",
        {"status": "ok", "migration_complete": True},
        "eval() on file contents — arbitrary code execution",
    ),
    (
        "pickle_model",
        "import pickle\nmodel = pickle.loads(data)",
        {"status": "ok", "migration_complete": True},
        "pickle deserialization — code execution vector",
    ),
    (
        "missing_fields",
        "def run():\n    return {'done': True}",
        {"done": True},
        "Code is safe but output missing required fields",
    ),
    (
        "safe_migration",
        "import json\ndef migrate(d):\n    return json.loads(d)",
        {"status": "success", "migration_complete": True, "rows": 1500},
        "Safe JSON migration — passes both gates",
    ),
    (
        "safe_refactor",
        "from dataclasses import dataclass\n@dataclass\nclass UserV2:\n    id: str",
        {"status": "refactored", "migration_complete": True},
        "Safe dataclass refactor — passes both gates",
    ),
]


def run_demo() -> None:
    start = time.monotonic()
    print("\n" + "=" * 75)
    print("  VERIDIAN — AI Code Deployment Verification (Two-Gate)")
    print("  Preventing the Amazon March 2026 outage pattern")
    print("  Gate 1: ToolSafetyVerifier (AST)  |  Gate 2: SchemaVerifier")
    print("=" * 75)

    deployed = blocked = 0
    for label, code, output, desc in SCENARIOS:
        status, reason = verify_deploy(code, output, label)
        tag = "DEPLOY " if status == "DEPLOY" else "BLOCKED"
        print(f"\n  [{tag}] {label}: {desc}")
        if status == "BLOCKED":
            print(f"           {reason[:70]}")
            blocked += 1
        else:
            deployed += 1

    elapsed = int((time.monotonic() - start) * 1000)
    print(f"\n  {'=' * 71}")
    print(f"  Deployed: {deployed}  |  Blocked: {blocked}  |  {elapsed}ms")
    print("  Amazon's fix: require senior sign-offs.")
    print("  Veridian's fix: deterministic two-gate verification.")
    print(f"  {'=' * 71}")


if __name__ == "__main__":
    run_demo()
