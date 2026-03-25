# Agent Drift Detection — Example

Demonstrates Veridian's `DriftDetectorHook` catching behavioral regression
in an AI agent pipeline.

## What it does

1. Pre-populates a JSONL history file with 7 stable runs (90% pass rate)
2. Simulates 3 degraded runs (70% pass rate, lower confidence, more retries)
3. After each degraded run, the hook detects drift and generates a report

## Run

```bash
python examples/drift-detection/run_drift_demo.py
```

## Expected output

- Runs 1-7: stable baseline (no drift detected)
- Run 8: first degraded run — drift WARNING
- Run 9-10: continued degradation — drift DRIFTING

A `drift_report.md` is generated in the temp directory showing which metrics
drifted, by how much, and recommended actions.
