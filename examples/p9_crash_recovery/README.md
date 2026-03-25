# P9 — Long-Running Agent Crash Recovery

## The Problem

METR 2025 benchmarks show that **50% of agent tasks fail within ~1 hour** of runtime.
LangGraph, CrewAI, and AutoGen all share the same critical gap: no crash recovery.

A 100-task database migration pipeline killed at task 73 silently **restarts from task 1**,
re-executing 72 tasks of work. Worse: duplicate writes to a production database can cause
data corruption, constraint violations, and compliance violations.

## The Veridian Solution

`TaskLedger` uses **atomic writes** (temp-file + `os.replace()`) for every state
transition. When a process is killed mid-task:

1. The ledger is **never left in a partial state** — `os.replace()` is POSIX-atomic
2. `reset_in_progress()` moves stale IN_PROGRESS tasks back to PENDING in one call
3. `VeridianRunner.run()` calls `reset_in_progress()` automatically as its **first step**
4. The pipeline resumes from exactly where it left off — no duplicated work

This is zero-configuration crash recovery. No checkpointing logic to write. No restart
policies to configure. The ledger is always consistent.

## What this example demonstrates

```
Phase 1: Create 50-task migration pipeline (extract → transform → load)
Phase 2: Run tasks 1–24 to completion (normal operation)
Phase 3: Simulate SIGKILL while task 25 is IN_PROGRESS
Phase 4: Verify ledger.json is not corrupted (atomic writes)
Phase 5: ledger.reset_in_progress() → task 25 back to PENDING
Phase 6: Resume with VeridianRunner → picks up from task 25
Phase 7: All 50 tasks DONE — 24 tasks saved, zero duplicated work
```

## How to run

```bash
cd /path/to/veridian
python examples/p9_crash_recovery/p9_crash_recovery.py
```

No API keys required — the example uses `MockProvider` with scripted responses.

## Key API

```python
# Every VeridianRunner.run() starts with this — automatic crash recovery
reset_count = ledger.reset_in_progress()
# → returns number of stale IN_PROGRESS tasks reset to PENDING

# The ledger write pattern (used everywhere):
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
    json.dump(data, f)
    tmp = Path(f.name)
os.replace(tmp, path)  # POSIX-atomic: readers never see partial writes
```

## Comparison

| Framework | Crash recovery | Mechanism |
|-----------|---------------|-----------|
| **Veridian** | ✅ Zero-config | `reset_in_progress()` + atomic writes |
| LangGraph | ❌ None | Full restart required |
| CrewAI | ❌ None | Full restart required |
| AutoGen | ❌ None | Full restart required |
