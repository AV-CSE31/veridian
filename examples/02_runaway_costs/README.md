# Problem 2: Runaway Cloud Costs

## The Incidents

**LangChain Agent Loop (Nov 2025):** Two LangChain agents (Analyzer + Verifier) entered an infinite conversation cycle. Ran for **11 days**, generating a **$47,000 bill**. Root cause: a misclassified error treated as "retry with different parameters." No cost ceiling. No loop detection. No timeout.

**Stolen API Key (2025):** A single compromised API key generated an **$82,000 bill in 48 hours**.

**Data Enrichment Agent (2025):** Generated **2.3 million unintended API calls** over a weekend. Only an external rate limiter stopped it.

**Industry impact:** IDC found **96% of enterprises** report AI costs exceeding initial projections. AnalyticsWeek estimates **$400 million** in unbudgeted AI cloud spend across the Fortune 500.

Sources: [TechStartups](https://techstartups.com/2025/11/14/ai-agents-horror-stories-how-a-47000-failure-exposed-the-hype-and-hidden-risks-of-multi-agent-systems/), [PointGuard AI](https://www.pointguardai.com/blog/when-a-stolen-ai-api-key-becomes-an-82-000-problem), [AnalyticsWeek](https://analyticsweek.com/finops-for-agentic-ai-cloud-cost-2026/)

## Root Cause

```
Agent encounters error or ambiguous state
  -> Retries with slightly different parameters
  -> Each retry costs tokens
  -> No maximum cost enforcement
  -> Loop runs for days (11 days in the LangChain case)
  -> $47,000 bill arrives
```

## Veridian's Fix

`CostGuardHook` — Veridian's real shipped hook that tracks cumulative token cost and raises `CostLimitExceeded` when the budget is exhausted.

- `before_task()`: checks if budget already exceeded — blocks BEFORE next task runs
- `after_task()`: accumulates cost from `result.token_usage.total_tokens`
- Warning at configurable threshold (default 80%)
- Hard halt is absolute — the agent cannot negotiate past it

## Run

```bash
cd examples/02_runaway_costs
python solution.py
pytest test_solution.py -v
```

## What This Proves

The $47,000 LangChain loop would have been halted at whatever ceiling was configured. The CostGuardHook doesn't depend on the agent stopping itself — it fires `CostLimitExceeded` from `before_task()`, which is caught by the runner, ending the run. The agent's opinion is irrelevant.
