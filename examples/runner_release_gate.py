"""Run a release gate task and export a verification report.

Run with:

    python examples/runner_release_gate.py

The example is deterministic and makes zero network calls.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import MockProvider, Task, TaskLedger, VeridianConfig, VeridianRunner
from veridian.core.report import validate_report_chain

RELEASE_CONTRACT = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = VeridianConfig(
            ledger_file=root / "ledger.json",
            progress_file=root / "progress.md",
            report_file=root / "verification-reports.jsonl",
        )
        ledger = TaskLedger(config.ledger_file, progress_file=str(config.progress_file))
        ledger.add(
            [
                Task(
                    id="release-2026-06",
                    title="Release gate",
                    description="Decide whether build 2026.06 can ship.",
                    verifier_id="schema",
                    verifier_config={"schema": RELEASE_CONTRACT},
                )
            ]
        )

        provider = MockProvider().script_veridian_result(
            structured={
                "decision": "ship",
                "risk": "low",
                "reason": "tests, lint, and verification passed",
            }
        )
        summary = VeridianRunner(ledger=ledger, provider=provider, config=config).run()
        validation = validate_report_chain(config.report_file)
        task = ledger.get("release-2026-06")

        print(f"done={summary.done_count} failed={summary.failed_count}")
        print(f"report_valid={validation.valid} reports={validation.checked_count}")
        print(
            f"report_hash={task.result.verification_report['report_hash'] if task.result else ''}"
        )


if __name__ == "__main__":
    main()
