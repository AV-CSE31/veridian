# Subsystem Status

Veridian's stable top-level surface (`from veridian import ...`) is intentionally
narrow (~40 symbols). Many production-grade subsystems ship inside the package
but are reached via their module path (`from veridian.X import ...`). This page
catalogues those subsystems so reviewers can distinguish "intentionally
optional" from "abandoned".

If a subsystem appears in this list and is in the `live` column, it has:

1. dedicated test coverage in `tests/`,
2. a documented `__all__` in its package `__init__.py`,
3. no migration to `veridian.experimental` planned.

## Subsystem catalogue

| Subsystem | Module path | Public via | Tests | Status |
| --- | --- | --- | --- | --- |
| Core runtime | `veridian.core.*` | top-level | `tests/unit/test_*` | live |
| Ledger | `veridian.ledger` | top-level | `test_ledger*` | live |
| Runner | `veridian.loop.runner` | top-level | `test_runner*` | live |
| Verifiers | `veridian.verify.*` | top-level | `test_verifiers*` | live |
| Hooks | `veridian.hooks.*` | top-level | `test_hooks*`, `test_drift_detector` | live |
| Providers | `veridian.providers.*` | top-level | `test_providers*` | live |
| Skills | `veridian.skills.*` | module path | `test_skill_library`, `test_blast_radius`, `test_quarantine` | live, opt-in |
| Drift detection | `veridian.hooks.builtin.drift_detector` | module path | `test_drift_detector` | live, opt-in |
| Eval pipeline | `veridian.eval.*` | module path | `test_adversarial_*`, `test_canary`, `test_reliability` | live, opt-in |
| Knowledge graph | `veridian.knowledge.*` | module path | `test_knowledge_graph` | live, opt-in |
| Graph executor | `veridian.graph.executor` | module path | `tests/integration/test_graph_semantics` | live, opt-in |
| Trusted executor | `veridian.loop.trusted_executor` | module path | `test_high_impact_gaps` | live |
| Policy engine | `veridian.policy.*` | module path | `test_policy_engine`, `test_policy_versioning` | live |
| Plugins | `veridian.plugins.*` | module path | `test_plugin_*` | live |
| Operator tooling | `veridian.operator.*` | module path | `test_operator_*` | live |
| Storage backends (Redis/Postgres) | `veridian.storage.*` | module path | integration suites | live, optional extras |
| Dashboard | `veridian.observability.dashboard` | module path | `test_dashboard_data` | live, optional extra |
| Integrations (LangGraph, CrewAI) | `veridian.integrations.{langgraph,crewai}` | module path | `test_langgraph_*`, `test_crewai_*` | certified |
| Integrations (Pydantic AI, Mastra, OpenAI Agents SDK, Inspect AI) | `veridian.integrations.{pydantic_ai,mastra,openai_agents,inspect_ai}` | module path | `test_v05_adapter_stubs` | preview |
| Self-improving intelligence | `veridian.intelligence.self_improving` | module path | `test_self_improving` | live, opt-in |
| Entropy GC | `veridian.entropy.gc` | top-level (lazy) | `test_entropy_gc` | live, opt-in |

## When to expose a subsystem at the top level

Add a symbol to `veridian.__all__` only when:

- it is stable across at least one minor release,
- the import is cheap (no heavy optional dependencies),
- it has claim-to-test traceability,
- it has a documented runbook or example.

Most subsystems should remain reachable via their module path. The top-level
namespace is reserved for the load-bearing primitives.

## When a subsystem belongs in `veridian.experimental`

Move to `veridian.experimental` only if:

- the API is expected to break in the next release,
- the subsystem lacks dedicated test coverage,
- the subsystem is documented as research-quality.

None of the subsystems in the catalogue meet that bar today.

## Deprecation policy

When a subsystem must be removed:

1. add a `DeprecationWarning` at import time for at least one release,
2. update `_DEPRECATED_EXPERIMENTAL_SYMBOLS` in `veridian/__init__.py`,
3. preserve the import path with a clear migration hint in the warning,
4. remove only after one full release cycle.
