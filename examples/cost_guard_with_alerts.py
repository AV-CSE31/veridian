"""End-to-end demo: trace export + alert escalation + verified outputs.

The LinkedIn-post answer (PR #9) claims Veridian's hook composition
solves the "agents burn money with no oversight" problem. This example
makes the claim concrete and **only uses the parts that work as
shipped**. It exercises:

  - ``JsonlTraceHook``  : one JSON record per lifecycle event
  - a custom ``AlertHook`` subclass that captures payloads in-process
  - ``TaskLedger`` + ``MockProvider`` : zero network calls, deterministic
  - the ``schema`` verifier : task 3 produces invalid output and fails
    verification, which is what the alert hook escalates

Important: the PR audit flags ``CostGuardHook`` (bug #1) as not
actually halting the runner — its ``CostLimitExceeded`` is swallowed
by ``HookRegistry.fire``. This example therefore demonstrates the
oversight surface (trace + alerts) without claiming the cost cap is
operational. Once that bug is fixed, the same composition gains the
spending-cap leg the LinkedIn post described.

Run with::

    python examples/cost_guard_with_alerts.py

The script writes ``./demo-ledger.json`` and ``./demo-trace.jsonl`` in
the current working directory and prints the captured alert payloads.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from veridian.core.task import Task
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.observability.alerts import AlertHook
from veridian.observability.trace import JsonlTraceHook, set_trace_id
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider

# ------ a capturing alert hook (in-process subclass) -----------------------
#
# Production code would use WebhookAlertHook with a Slack/PagerDuty URL.
# A capturing subclass keeps the example deterministic and offline.


class CapturingAlertHook(AlertHook):
    id = "capturing_alert"

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    def emit(self, alert: dict[str, Any]) -> None:
        self.alerts.append(alert)


# ------ the demo --------------------------------------------------------


def build_provider() -> MockProvider:
    """Two valid responses, then one that fails the schema verifier."""
    good = (
        "<veridian:result>"
        '{"summary":"ok","structured":{"decision":"ship","risk":"low"}}'
        "</veridian:result>"
    )
    bad = '<veridian:result>{"summary":"bad","structured":{"decision":"maybe"}}</veridian:result>'
    return MockProvider().script(
        [
            LLMResponse(content=good, input_tokens=120, output_tokens=40),
            LLMResponse(content=good, input_tokens=120, output_tokens=40),
            LLMResponse(content=bad, input_tokens=120, output_tokens=40),
        ]
    )


RELEASE_SCHEMA = {
    "type": "object",
    "required": ["decision", "risk"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}


def build_tasks() -> list[Task]:
    return [
        Task(
            title=f"release-gate-{n}",
            description="Decide whether to ship the build.",
            verifier_id="schema",
            verifier_config={"schema": RELEASE_SCHEMA},
        )
        for n in range(3)
    ]


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="veridian-demo-"))
    ledger_path = workdir / "demo-ledger.json"
    trace_path = workdir / "demo-trace.jsonl"

    ledger = TaskLedger(path=str(ledger_path))
    ledger.add(build_tasks())

    hooks = HookRegistry()
    trace_hook = JsonlTraceHook(trace_path)
    alert_hook = CapturingAlertHook()
    hooks.register(trace_hook)
    hooks.register(alert_hook)

    # Pin a deterministic trace id so the JSONL output is reproducible.
    set_trace_id("demo-run-1")

    # Import inside main() so the example doesn't pay the runner's
    # ContextManager import cost when only the helpers are being read.
    from veridian.loop.runner import VeridianRunner

    runner = VeridianRunner(
        ledger=ledger,
        provider=build_provider(),
        hooks=hooks,
    )
    summary = runner.run()

    # --- what the run produced ---
    print(f"workdir         : {workdir}")
    print(f"tasks total     : {summary.total_tasks}")
    print(f"tasks done      : {summary.done_count}")
    print(f"tasks failed    : {summary.failed_count}")
    print(f"alerts captured : {len(alert_hook.alerts)}")
    for alert in alert_hook.alerts:
        kind = alert.get("kind")
        task_id = alert.get("task_id", "")
        err = alert.get("error", "")
        print(f"  - kind={kind} task={task_id} error={err[:80]!r}")

    # --- trace file recap ---
    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    print(f"trace records   : {len(lines)} -> {trace_path}")
    for line in lines[:3]:
        record = json.loads(line)
        print(f"  - {record.get('hook_method'):<12} {record.get('event_type')}")


if __name__ == "__main__":
    main()
