# Drift Detection

Cross-run behavioral drift is gap **G3** in the
[Veridian threat model](threat-model.md): two runs on the same task produce
subtly different outputs that still pass individual verifiers, but accumulate
into silent reproducibility loss across deployments.

This runbook is for production operators who want to enable drift detection,
read its reports, and react to flagged regressions.

## What Veridian detects

`DriftDetectorHook` is a read-only hook that never mutates ledger state. It
records per-run snapshots and compares the current run against a rolling
historical window using z-score and threshold methods.

Tracked metrics:

- verifier pass/fail rates (per verifier id)
- average confidence score across self-consistency runs
- total token usage per task
- retry counts
- failure-error fingerprints

## Enable

Drift detection is opt-in via `VeridianConfig`:

```python
from veridian import VeridianConfig, VeridianRunner

config = VeridianConfig(
    drift_history_file="ops/drift_history.jsonl",
    drift_window=10,           # compare current run against last 10
    drift_threshold=0.15,      # min change magnitude to flag (0.0–1.0)
)

runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
summary = runner.run()
```

When `drift_history_file` is set, `VeridianRunner` auto-registers
`DriftDetectorHook` with the configured window and threshold. No code change
required.

## Manual hook registration

If you want full control (custom z-threshold, separate report path), register
the hook explicitly:

```python
from veridian import HookRegistry
from veridian.hooks.builtin.drift_detector import DriftDetectorHook

hooks = HookRegistry()
hooks.register(
    DriftDetectorHook(
        history_file="ops/drift_history.jsonl",
        window=10,
        threshold=0.15,
        z_threshold=2.0,
        report_path="ops/drift_report.md",
    )
)

runner = VeridianRunner(ledger=ledger, provider=provider, hooks=hooks)
```

## Reading the report

After each run, inspect `last_report` on the hook (or open the configured
`report_path`):

```python
report = drift_hook.last_report
if report and report.signals:
    for signal in report.signals:
        print(f"{signal.metric}: {signal.baseline} → {signal.current} (z={signal.z_score:.2f})")
```

Each `DriftSignal` carries:

- `metric` — name of the drifted metric
- `baseline` — historical mean over the window
- `current` — value from the current run
- `z_score` — how many standard deviations from baseline
- `severity` — `"info"`, `"warn"`, `"critical"` depending on threshold breach

## Failure mode

The hook **does not block** task transitions. It surfaces evidence. If you
want drift to block a release, gate your deploy pipeline on the presence of
non-info signals in `drift_report.md`.

## When to widen the window

A 10-run window catches deployment-shift regressions cleanly but is noisy
for low-volume queues. For sub-daily cadence consider `window=30`.
Lower `threshold` to `0.05` for safety-critical workflows where any drift
matters.

## When NOT to use drift detection

- Single-task one-shot scripts (no baseline yet).
- Highly stochastic agent behavior where the verifier is the only contract —
  drift signals will be persistent false-positives.
- Replay-only debugging runs (snapshots from replay are not representative
  of production behavior; exclude with a separate `history_file`).

## Related

- [Threat model gap G3](threat-model.md)
- [`veridian.hooks.builtin.drift_detector`](../../veridian/hooks/builtin/drift_detector.py)
- [`CrossRunConsistencyHook`](../../veridian/hooks/builtin/cross_run_consistency.py) — paired hook for per-task replay consistency
