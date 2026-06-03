"""Verify that an agent-produced artifact exists before marking work done.

Run with:

    python examples/artifact_verification_gate.py

The example is deterministic and makes zero network calls.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import MockProvider, Task, TaskLedger, VeridianConfig, VeridianRunner


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "release-summary.md"
        artifact.write_text("Release 2026.06 passed all gates.\n", encoding="utf-8")

        config = VeridianConfig(
            ledger_file=root / "ledger.json",
            progress_file=root / "progress.md",
        )
        ledger = TaskLedger(config.ledger_file, progress_file=str(config.progress_file))
        ledger.add(
            [
                Task(
                    id="artifact-gate",
                    title="Artifact gate",
                    description="Verify the release summary artifact exists and is non-empty.",
                    verifier_id="file_exists",
                    verifier_config={"files": [str(artifact)], "check_non_empty": True},
                )
            ]
        )

        provider = MockProvider().script_veridian_result(
            structured={"summary": "release summary written"}
        )
        summary = VeridianRunner(ledger=ledger, provider=provider, config=config).run()
        task = ledger.get("artifact-gate")
        evidence = task.result.verification_evidence if task.result else {}

        print(f"done={summary.done_count} failed={summary.failed_count}")
        print(f"artifact_verified={artifact.name in str(evidence)}")


if __name__ == "__main__":
    main()
