"""Minimal Veridian release-gate example.

Run with:

    python examples/release_gate.py

The example is deterministic: it uses MockProvider, writes to a temporary
ledger, and makes zero network calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import MockProvider, Task, TaskLedger, VeridianRunner

RELEASE_CONTRACT = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}


def _release_task(title: str) -> Task:
    return Task(
        title=title,
        description=(
            "Decide whether build 2026.05 can ship. Return decision, risk, "
            "and reason in the Veridian result block."
        ),
        verifier_id="schema",
        verifier_config={"schema": RELEASE_CONTRACT},
    )


def main() -> None:
    logging.getLogger("veridian.verify.base").setLevel(logging.ERROR)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = TaskLedger(root / "ledger.json", progress_file=str(root / "progress.md"))
        ledger.add(
            [
                _release_task("Release gate: good evidence"),
                _release_task("Release gate: missing evidence"),
            ]
        )

        provider = MockProvider()
        provider.script_veridian_result(
            structured={
                "decision": "ship",
                "risk": "low",
                "reason": "tests, lint, and verification passed",
            }
        )
        provider.script_veridian_result(
            structured={
                "decision": "ship",
                "risk": "low",
            }
        )

        summary = VeridianRunner(ledger=ledger, provider=provider).run()
        print(summary.to_dict())

        for task in ledger.list():
            error = f" error={task.last_error!r}" if task.last_error else ""
            print(f"{task.title}: {task.status.value}{error}")


if __name__ == "__main__":
    main()
