# Problem 7: 95% AI Pilot Failure — Silent Behavioral Drift

## The Problem

**MIT study:** 95% of enterprise AI pilots deliver zero measurable return. Not because models are incapable — because nobody detects when they silently degrade.

**The drift pattern:**
- Week 1-4: Agent works perfectly. 95% pass rate. Everyone is happy.
- Week 5-8: Pass rate drops to 88%. Nobody notices — it's still "mostly working."
- Week 12: Pass rate at 72%. Token costs doubled. Confidence scores eroded. A quarterly review catches it — months too late.

**Research basis:**
- arXiv 2601.04170 ("Agent Drift"): formalizes three types — semantic drift, data drift, concept drift
- February 2026 paper: reliability doesn't improve uniformly with capability
- Chanl AI: "Drift is a month-three-plus problem — launch testing misses it entirely"
- Agent Stability Index (ASI): 12 dimensions for tracking drift

Sources: [Chanl AI](https://www.chanl.ai/blog/agent-drift-silent-degradation), [arXiv](https://arxiv.org/html/2601.04170v1), [AllDaysTech](https://alldaystech.com/guides/artificial-intelligence/model-drift-detection-monitoring-response)

## Root Cause

```
Agent deployed to production
  -> Initial evaluation shows high performance
  -> Model updates, data distribution shifts, or prompt decay occurs
  -> Pass rates slowly erode (invisible without historical comparison)
  -> Token costs increase (agent retrying more, hallucinating more)
  -> By the time a human notices, months of degraded output are in production
```

## Veridian's Fix

**DriftDetectorHook** — compares current run metrics against a historical window using Bayesian analysis:
- Completion rate
- Confidence mean
- Retry rate
- Token consumption
- Failure mode clustering

Flags statistically significant degradation, not just any change.

**BehavioralFingerprint** — 7-dimensional per-run signature:
- Action distribution, output structure, token profile, verification pattern, tool selection, latency, confidence
- Cosine similarity between consecutive runs
- Catches subtle behavioral shifts that aggregate metrics miss

## Run

```bash
cd examples/07_pilot_failure
python solution.py
pytest test_solution.py -v
```

## What This Proves

A degraded run (72% completion vs 95% baseline) is flagged as DRIFTING with specific signals naming which metrics degraded and by how much. The fingerprint catches behavioral pattern changes (different tools, different output structure) even when aggregate pass rates look similar. This is the detection layer that the 95% of failed pilots were missing.
