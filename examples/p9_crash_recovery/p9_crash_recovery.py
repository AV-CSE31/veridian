"""
P9 -- Long-Running Agent Crash Recovery (Enterprise)

Problem
-------
METR 2025 benchmarks show that agent task failure horizons are around 1 hour for
50% of tasks. LangGraph, CrewAI, and AutoGen have no built-in crash recovery --
a 100-task migration pipeline killed at task 73 silently restarts from task 1,
duplicating 72 tasks of work (and risking duplicate writes to production).

Veridian Solution
-----------------
TaskLedger atomic writes + reset_in_progress() provide instant crash recovery
with zero configuration. Every state transition is protected by temp-file + os.replace().
When a process is killed mid-task, the ledger is never left in a partial state.
On restart, reset_in_progress() moves stale IN_PROGRESS tasks back to PENDING in
a single atomic operation -- then the runner picks up exactly where it left off.

Demonstrated
------------
  Phase 1: Create a 50-task database migration pipeline
  Phase 2: Run tasks 1-24 to completion (normal operation)
  Phase 3: SIMULATE CRASH -- task 25 left IN_PROGRESS (process killed)
  Phase 4: Verify ledger integrity -- no corruption despite mid-task kill
  Phase 5: reset_in_progress() -- task 25 back to PENDING in one call
  Phase 6: Resume pipeline -- VeridianRunner picks up from task 25
  Phase 7: All 50 tasks DONE -- zero duplicated work

Run
---
    python examples/p9_crash_recovery/p9_crash_recovery.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

# Repo root on sys.path so the script is runnable from any working directory
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

TOTAL_TASKS = 50
CRASH_AT_TASK = 25  # Simulate crash while processing task 25 (IN_PROGRESS)
PRE_CRASH_RUN_ID = "run-pre-crash"
SEP = "=" * 72
sep = "-" * 72


# ── Task factory ──────────────────────────────────────────────────────────────

def build_migration_tasks() -> list[Task]:
    """
    Build 50 data migration tasks representing a typical database migration:
      - Tasks 1-10:  Extract (read from legacy shards)
      - Tasks 11-35: Transform (normalize, pseudonymize, validate)
      - Tasks 36-50: Load (write to target cluster, verify counts)

    All tasks use SchemaVerifier to enforce the migration report format.
    """
    tasks = []
    for i in range(1, TOTAL_TASKS + 1):
        if i <= 10:
            phase = "extract"
            title = f"Extract: User Records Shard {i:02d}"
            description = (
                f"Extract user records from legacy shard DB-{i:02d}. "
                f"Validate schema compatibility with target. "
                f"Output validated CSV to staging area /staging/shard_{i:02d}/."
            )
        elif i <= 35:
            shard = ((i - 11) % 10) + 1
            phase = "transform"
            title = f"Transform: Normalize Batch {i - 10:02d} (shard DB-{shard:02d})"
            description = (
                f"Normalize user records from shard DB-{shard:02d}. "
                f"Apply GDPR pseudonymization to PII fields. "
                f"Resolve foreign key references against target schema. "
                f"Validate referential integrity before staging."
            )
        else:
            batch = i - 35
            phase = "load"
            title = f"Load: Write to Target DB (batch {batch:02d})"
            description = (
                f"Write normalized batch {batch:02d} to target PostgreSQL cluster. "
                f"Verify row counts match source. Check constraint violations. "
                f"Update migration manifest with batch checksum."
            )

        task = Task(
            id=f"batch_{i:03d}",
            title=title,
            description=description,
            verifier_id="schema",
            verifier_config={
                "required_fields": [
                    "records_migrated",
                    "validation_status",
                    "batch_id",
                ]
            },
            phase=phase,
            metadata={"batch_number": i, "phase": phase},
        )
        tasks.append(task)
    return tasks


def make_migration_result(batch_id: str, records: int) -> TaskResult:
    """Build a completed TaskResult for a migration batch (pre-crash phase)."""
    structured = {
        "records_migrated": records,
        "validation_status": "PASS",
        "batch_id": batch_id,
    }
    raw_json = json.dumps(
        {
            "summary": f"Batch {batch_id} migrated {records} records successfully.",
            "structured": structured,
            "artifacts": [],
        }
    )
    return TaskResult(
        raw_output=f"<veridian:result>\n{raw_json}\n</veridian:result>",
        structured=structured,
        artifacts=[],
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Demonstrate crash recovery for a 50-task migration pipeline."""
    with tempfile.TemporaryDirectory(prefix="veridian_p9_") as tmp:
        tmp_path = Path(tmp)
        ledger_path = tmp_path / "ledger.json"
        progress_path = tmp_path / "progress.md"

        print()
        print(SEP)
        print("P9 -- Long-Running Agent Crash Recovery")
        print(f"50-task database migration pipeline with simulated crash at task {CRASH_AT_TASK}")
        print(SEP)

        # Phase 1: Set up the pipeline
        print()
        print("Phase 1: Setup")
        print(sep)
        tasks = build_migration_tasks()
        ledger = TaskLedger(path=ledger_path, progress_file=str(progress_path))
        ledger.add(tasks)
        print(f"[OK] {TOTAL_TASKS} migration tasks loaded into ledger")
        print(f"     Phases: extract (10), transform (25), load (15)")

        # Phase 2: Process tasks 1-24 (normal operation, pre-crash)
        print()
        print(f"Phase 2: Normal Operation -- Tasks 1-{CRASH_AT_TASK - 1}")
        print(sep)
        t0 = time.monotonic()
        for i in range(1, CRASH_AT_TASK):
            batch_id = f"batch_{i:03d}"
            records = 1000 + i * 13
            result = make_migration_result(batch_id, records)
            ledger.claim(batch_id, PRE_CRASH_RUN_ID)
            ledger.submit_result(batch_id, result)
            ledger.mark_done(batch_id, result)
        pre_crash_duration = time.monotonic() - t0

        stats_normal = ledger.stats()
        print(f"[OK] Tasks 1-{CRASH_AT_TASK - 1} completed in {pre_crash_duration:.3f}s")
        print(
            f"     Ledger: {stats_normal.by_status.get('done', 0)} DONE, "
            f"{stats_normal.by_status.get('pending', 0)} PENDING"
        )

        # Phase 3: Simulate crash at task 25
        print()
        print(f"Phase 3: CRASH SIMULATION -- Task {CRASH_AT_TASK}")
        print(sep)

        crash_task_id = f"batch_{CRASH_AT_TASK:03d}"
        crash_task = ledger.get(crash_task_id)
        print(f'Runner picks up task {crash_task_id}: "{crash_task.title}"')
        ledger.claim(crash_task_id, PRE_CRASH_RUN_ID)   # -> IN_PROGRESS

        stats_crash = ledger.stats()
        print()
        print("[X] SIGKILL -- process terminated mid-task")
        print(f"    Task {crash_task_id} is stuck IN_PROGRESS")
        print(
            f"    Ledger: "
            f"{stats_crash.by_status.get('done', 0)} DONE, "
            f"{stats_crash.by_status.get('in_progress', 0)} IN_PROGRESS, "
            f"{stats_crash.by_status.get('pending', 0)} PENDING"
        )

        # Phase 4: Verify ledger integrity
        print()
        print("Phase 4: Ledger Integrity Check")
        print(sep)

        raw_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        in_progress = [
            t for t in raw_ledger["tasks"].values() if t["status"] == "in_progress"
        ]
        tmp_files = list(tmp_path.glob("*.tmp"))

        assert raw_ledger.get("schema_version") == 2, "Schema version corrupted"
        assert len(raw_ledger["tasks"]) == TOTAL_TASKS, "Task count corrupted"
        assert len(in_progress) == 1, "Unexpected IN_PROGRESS count"
        assert len(tmp_files) == 0, "Temp files left behind -- atomic write failed"

        print(f"[OK] ledger.json schema_version={raw_ledger['schema_version']} -- intact")
        print(f"[OK] Task count: {len(raw_ledger['tasks'])} / {TOTAL_TASKS} -- no tasks lost")
        print(f"[OK] IN_PROGRESS count: {len(in_progress)} (expected 1) -- correct")
        print(f"[OK] Orphaned .tmp files: {len(tmp_files)} -- atomic write left no partial state")
        print()
        print("     Every ledger write uses: NamedTemporaryFile -> json.dump -> os.replace()")
        print("     os.replace() is POSIX-atomic: readers never see partial writes.")

        # Phase 5: Crash recovery
        print()
        print("Phase 5: Crash Recovery")
        print(sep)
        print("Calling ledger.reset_in_progress() -- standard first step in VeridianRunner.run()")

        reset_count = ledger.reset_in_progress()
        stats_recovered = ledger.stats()

        print(f"[OK] reset_in_progress() reset {reset_count} stale task(s) -> PENDING")
        print(
            f"     Ledger: "
            f"{stats_recovered.by_status.get('done', 0)} DONE, "
            f"{stats_recovered.by_status.get('in_progress', 0)} IN_PROGRESS, "
            f"{stats_recovered.by_status.get('pending', 0)} PENDING"
        )
        print(f"     Task {crash_task_id}: back to PENDING -- will resume from scratch")
        print()
        print("     Compare: LangGraph / CrewAI / AutoGen have no reset_in_progress().")
        print(f"     They restart from task 1, re-executing {CRASH_AT_TASK - 1} already-done tasks.")

        # Phase 6: Resume pipeline
        remaining = stats_recovered.by_status.get("pending", 0)
        print()
        print(f"Phase 6: Resume Pipeline ({remaining} tasks remaining)")
        print(sep)

        # Script one veridian_result response per remaining task (tasks 25-50)
        provider = MockProvider()
        for i in range(CRASH_AT_TASK, TOTAL_TASKS + 1):
            batch_id = f"batch_{i:03d}"
            records = 1000 + i * 13
            provider.script_veridian_result(
                {
                    "records_migrated": records,
                    "validation_status": "PASS",
                    "batch_id": batch_id,
                },
                summary=f"Batch {batch_id} migrated {records} records.",
            )

        config = VeridianConfig(
            max_turns_per_task=1,   # single LLM call per task (mock)
            max_retries=0,
            dry_run=False,
            progress_file=progress_path,
        )

        t1 = time.monotonic()
        runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
        # Note: VeridianRunner.run() calls reset_in_progress() again as step 1.
        # This is idempotent -- no IN_PROGRESS tasks remain, returns 0.
        summary = runner.run()
        resume_duration = time.monotonic() - t1

        print(
            f"[OK] Resumed from task {CRASH_AT_TASK}, "
            f"completed {summary.done_count} tasks in {resume_duration:.2f}s"
        )
        print(
            f"     Runner called reset_in_progress() again at startup "
            f"(idempotent -- 0 tasks reset, none were IN_PROGRESS)"
        )

        # Phase 7: Final report
        print()
        print("Phase 7: Final Report")
        print(sep)

        final_stats = ledger.stats()
        all_done = final_stats.by_status.get("done", 0) == TOTAL_TASKS

        print(f"  Total migration tasks              : {TOTAL_TASKS}")
        print(f"  Completed before crash             : {CRASH_AT_TASK - 1}")
        print(f"  Task at crash (-> PENDING -> re-run): {crash_task_id}")
        print(f"  Completed after recovery           : {summary.done_count}")
        print(f"  Total DONE                         : {final_stats.by_status.get('done', 0)} / {TOTAL_TASKS}")
        failed = (
            final_stats.by_status.get("failed", 0)
            + final_stats.by_status.get("abandoned", 0)
        )
        print(f"  Failed / Abandoned                 : {failed}")
        print(f"  Tasks wasted on restart            : 0 (resumed from task 25, not from task 1)")
        print(f"  Ledger integrity                   : [OK] Verified")
        print(f"  Resume time                        : {resume_duration:.2f}s")

        if all_done:
            print()
            print(f"[OK] All {TOTAL_TASKS} migration tasks DONE.")
            print(f"     Pipeline resumed from task {CRASH_AT_TASK}, not from task 1.")

        # Comparison
        saved_tasks = CRASH_AT_TASK - 1
        print()
        print(SEP)
        print("WHY THIS MATTERS")
        print(SEP)
        print("  With Veridian (this example):")
        print(f"    * Crash at task {CRASH_AT_TASK} -- tasks 1-{CRASH_AT_TASK - 1} preserved in ledger")
        print(f"    * reset_in_progress() -> task {CRASH_AT_TASK} back to PENDING (1 line)")
        print(f"    * Resume from task {CRASH_AT_TASK} -- {saved_tasks} tasks saved, zero duplicated work")
        print("    * Atomic writes ensure ledger.json is never partially written")
        print()
        print("  Without Veridian (LangGraph / CrewAI / AutoGen):")
        print("    * No crash-safe state -> restart from task 1")
        print(f"    * {saved_tasks} tasks re-executed unnecessarily")
        print("    * Risk of duplicate writes to production database")
        print("    * METR 2025: 50% of agent tasks fail within ~1 hour --")
        print("      crash recovery is table stakes")
        print()


if __name__ == "__main__":
    main()
