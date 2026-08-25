# Veridian assurance benchmark

`sota_assurance_bench.py` is the versioned adversarial and durability harness
for Veridian's assurance, effects, adapter, and mathematical-verification
surfaces. It emits one self-contained JSON report with the exact configuration,
environment, deterministic schedule fingerprint, case and outcome
distributions, observation counts, and latency percentiles.

Bounded CI smoke:

```console
uv run python benchmarks/sota_assurance_bench.py \
  --profile smoke --iterations 8 --seed 20260819 --concurrency 4
```

Build and inspect the explicit 100,000-schedule campaign without executing it:

```console
uv run python benchmarks/sota_assurance_bench.py \
  --profile campaign --iterations 100000 --seed 20260819 --dry-run
```

Execute that campaign and retain the report:

```console
uv run python benchmarks/sota_assurance_bench.py \
  --profile campaign --iterations 100000 --seed 20260819 \
  --concurrency 16 --output benchmark-results/assurance-campaign.json
```

Use `--list-cases` to discover case identifiers and `--cases` to run a focused
comma-separated subset. The SQLite crash case uses abrupt process exits at
committed API boundaries. It does not claim to model a VM power cut or prove
storage-controller/filesystem correctness.

A passing report means no failure was observed in the disclosed finite run. It
is never evidence of zero residual risk.
