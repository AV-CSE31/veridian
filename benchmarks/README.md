# Veridian Reliability Benchmarks

Two runnable measurements of the properties Veridian claims, designed around
failure modes documented in the agent-reliability literature but not covered
by any public benchmark: *claimed-done vs actually-done* ("victory
declaration bias") and *harness state durability under crash*.

Both scripts need only the base install (`pip install veridian-ai`), print a
JSON report, and exit non-zero on any violation, so they can run in CI.

## crash_recovery_bench.py

SIGKILLs a ledger-writer subprocess at a random point mid-stream, reopens the
ledger, and reconciles it against the operations the worker had already seen
acknowledged. Reports acknowledged-op loss, ledger corruption, orphan temp
files, and how many `IN_PROGRESS` tasks the crash-recovery contract reset.

```bash
python benchmarks/crash_recovery_bench.py --runs 20
```

Scope: SIGKILL validates atomic-rename semantics and ack ordering. It cannot
simulate power loss (the kernel page cache survives process death); that is
covered by the fsync in `TaskLedger._write_raw` and needs a power-cut rig to
test end to end.

## verified_completion_bench.py

Simulates a worker that always claims success in prose while a configurable
fraction of its structured outputs violate the task contract. Drives the same
result stream through a trust-the-claim baseline and through Veridian's
verification gate, and reports the false-DONE rate of each.

```bash
python benchmarks/verified_completion_bench.py --tasks 200 --defect-rate 0.25
```

Expected outcome: the baseline's false-DONE rate tracks the defect rate; the
gated harness records zero false DONEs and converts every defective result to
`FAILED`.
