# AGENTS.md — Veridian Project Map

> Navigational map for AI agents. Read CLAUDE.md for rules. Read this file to orient.
> When this file and CLAUDE.md conflict, CLAUDE.md wins.

---

## What This Project Is

`veridian` — Deterministic verification infrastructure for reliable long-running AI agents.
The CI/CD equivalent for agentic systems.

- PyPI: `veridian` | GitHub: `veridian-ai/veridian` | MIT | Python ≥ 3.11

---

## Repository Layout

```
veridian/
├── core/
│   ├── task.py              Task, TaskStatus (state machine), TaskResult, TaskPriority, LedgerStats
│   ├── events.py            40+ typed events: RunStarted, TaskCompleted, VerificationFailed, SLABreached…
│   ├── exceptions.py        VeridianError hierarchy: InvalidTransition, BlockedCommand, CostLimitExceeded…
│   ├── quality_gate.py      TaskQualityGate (5-axis), TaskGraph (cycle detection, topo sort)
│   ├── config.py            ✅ BUILT — VeridianConfig (model, max_turns, dry_run, ledger_file, …)
│   └── sla.py               SLAPolicy, DegradationStrategy  [Phase 4+]
│
├── ledger/
│   ├── ledger.py            TaskLedger: add/claim/submit_result/mark_done/mark_failed/reset_in_progress/get_next
│   └── snapshot.py          LedgerSnapshot, SnapshotManager — delta-encoded point-in-time  [Phase 10]
│
├── verify/
│   ├── base.py              BaseVerifier ABC, VerificationResult, VerifierRegistry (entry-point autodiscovery)
│   ├── policy.py            PolicyEngine — YAML/JSON → runtime verifier chain  [Week 11]
│   ├── templates/           50+ YAML templates  [Week 11]
│   └── builtin/
│       ├── __init__.py      Registers all 10 verifiers
│       ├── semantic_grounding.py   ✅ BUILT — 3 hallucination classes
│       ├── confidence.py           ✅ BUILT — ConfidenceScore, SelfConsistencyVerifier
│       ├── bash.py                 ✅ BUILT — BashExitCodeVerifier
│       ├── quote.py                ✅ BUILT — QuoteMatchVerifier — PDF/txt/md/docx
│       ├── schema.py               ✅ BUILT — SchemaVerifier — Pydantic or JSON Schema
│       ├── http.py                 ✅ BUILT — HttpStatusVerifier
│       ├── file_exists.py          ✅ BUILT — FileExistsVerifier
│       ├── composite.py            ✅ BUILT — CompositeVerifier — AND chain
│       ├── any_of.py               ✅ BUILT — AnyOfVerifier — OR chain
│       └── llm_judge.py            ✅ BUILT — LLMJudgeVerifier — ALWAYS inside Composite
│
├── hooks/
│   ├── base.py              ✅ BUILT — BaseHook ABC — 11 lifecycle methods, all default no-op
│   ├── registry.py          ✅ BUILT — HookRegistry — fire/register, autodiscovery, errors always caught
│   └── builtin/
│       ├── cross_run_consistency.py  ✅ BUILT — claim monitoring, contradiction detection
│       ├── logging_hook.py           ✅ BUILT — priority=0, structured logging
│       ├── cost_guard.py             ✅ BUILT — raises CostLimitExceeded
│       ├── human_review.py           ✅ BUILT — raises HumanReviewRequired
│       ├── rate_limit.py             ✅ BUILT — sliding window, sleeps, never raises
│       ├── slack.py                  ✅ BUILT — httpx POST, fails silently without webhook_url
│       ├── identity_guard.py         secrets injection + scrubbing, priority=5  [Week 12]
│       ├── sla_monitor.py            wall-clock/token SLA tracking  [Phase 4+]
│       └── model_downgrader.py       70%→cheaper model, 90%→strategy  [Phase 4+]
│
├── context/
│   ├── window.py            ✅ BUILT — TokenWindow — budget/used/fits/consume/remaining_chars/pct_used
│   ├── manager.py           ✅ BUILT — ContextManager — frozen 6-step assembly
│   ├── compactor.py         ✅ BUILT — ContextCompactor — triggers at 85%, preserves last 3 exchanges
│   ├── relevance.py         RelevanceScorer — 5-weight no-LLM scoring  [Phase 8]
│   ├── budget.py            ContextBudgetAllocator — % allocation per section  [Phase 8]
│   └── summarizer.py        ProgressiveSummarizer — cheap-model summaries to progress.md  [Phase 8]
│
├── agents/
│   ├── base.py              ✅ BUILT — BaseAgent ABC
│   ├── worker.py            ✅ BUILT — WorkerAgent — agentic loop with <veridian:result> parsing
│   ├── initializer.py       ✅ BUILT — InitializerAgent — validates task spec
│   ├── reviewer.py          ✅ BUILT — ReviewerAgent — optional result review
│   ├── registry.py          AgentRegistry — role → BaseAgent  [Phase 7+]
│   ├── replanner.py         ReplannerAgent — adds/reprioritizes, never touches DONE  [Phase 11]
│   └── prompts/
│       ├── worker.md        ✅ BUILT
│       ├── initializer.md   [Phase 4]
│       ├── reviewer.md      [Phase 4]
│       └── replanner.md     [Phase 11]
│
├── loop/
│   ├── runner.py            ✅ BUILT — VeridianRunner, RunSummary (SIGINT-safe, frozen sequence)
│   ├── parallel_runner.py   ✅ BUILT — ParallelRunner — asyncio + semaphore, bounded concurrency
│   ├── trusted_executor.py  ✅ BUILT — TrustedExecutor (blocklist), OutputSanitizer (5 layers), BashOutput
│   ├── dag_scheduler.py     DAGScheduler — multi-agent DAG  [Phase 7+]
│   └── agent_context.py     AgentContextScope — per-agent isolation  [Phase 7+]
│
├── providers/
│   ├── litellm_provider.py  ✅ BUILT — CircuitBreaker → retry → fallback → context guard
│   └── mock_provider.py     ✅ BUILT — script/script_text/script_veridian_result/respond_when
│
├── secrets/
│   ├── base.py              SecretsProvider ABC  [Week 12]
│   ├── env.py               EnvSecretsProvider — dev/CI only  [Week 12]
│   ├── aws.py               AWSSecretsProvider  [Week 12]
│   ├── vault.py             VaultSecretsProvider  [Week 12]
│   └── azure.py             AzureSecretsProvider  [Week 12]
│
├── storage/
│   ├── local_json.py        LocalJSONStorage — atomic + FileLock, zero extra deps  [Phase 6]
│   ├── redis_backend.py     RedisStorage — SETNX lock, sorted set for get_next()  [Phase 6]
│   └── postgres_backend.py  PostgresStorage — pg_advisory_lock, SKIP LOCKED  [Phase 6]
│
├── observability/
│   ├── tracer.py            VeridianTracer — OTel GenAI v1.37+ + JSONL fallback  [Phase 6]
│   ├── dashboard.py         FastAPI SSE — port 7474  [Phase 6]
│   ├── cost_analytics.py    CostAnalytics  [Phase 4+]
│   ├── audit.py             AuditChain, SignedEvent — SHA-256 chained  [Phase 5+]
│   └── compliance.py        ComplianceReportGenerator — SOC2/HIPAA/NIST  [Phase 5+]
│
├── entropy/
│   └── gc.py                EntropyGC — 9 checks, entropy_report.md only, NEVER mutates  [Phase 6]
│
├── protocols/
│   ├── mcp.py               MCPToolProvider — OutputSanitizer gate on every tool call  [Phase 6+]
│   ├── a2a.py               A2AAdapter, VeridianAgentCard  [Phase 6+]
│   └── delegate.py          DelegateTask  [Phase 6+]
│
├── eval/
│   ├── suite.py             EvalSuite — 5 reliability dimensions  [Phase 9]
│   ├── dimensions.py        completion_rate, verification_pass_rate, crash_recovery_rate…
│   ├── suites/              legal(25), code(40), compliance(30), pipeline(35), moderation(20)
│   └── adapters/            Competitor adapters for benchmarking
│
├── skills/
│   ├── models.py            ✅ BUILT — Skill, SkillStep, SkillCandidate, Bayesian reliability scoring
│   ├── store.py             ✅ BUILT — SkillStore: atomic JSON, cosine similarity, ranked by bayesian_lower_bound
│   ├── extractor.py         ✅ BUILT — SkillExtractor: DONE tasks → confidence filter → LLM extraction
│   ├── admission.py         ✅ BUILT — SkillAdmissionControl: 4-gate (confidence, retries, steps, dedup)
│   ├── library.py           ✅ BUILT — SkillLibrary facade: post_run(), query(), record_outcome()
│   └── prompts/
│       ├── extract.md       ✅ BUILT — LLM prompt for skill extraction from completed tasks
│       └── reuse.md         ✅ BUILT — LLM prompt for verified procedure injection
│
├── memory/
│   ├── consolidator.py      MemoryConsolidator — episodic→semantic  [Phase 13]
│   └── skill_library.py     superseded by skills/ above
│
└── cli/
    ├── main.py              Typer — 8 commands, rich output  [Phase 7]
    └── replay.py            veridian replay  [Phase 10]

docs/
├── architecture.md          System design + rationale
├── concepts.md              Ledger, Verifier, Hook, Provider explained
├── production-hardening.md  Circuit breakers, SLA, cost, failure playbooks
├── research-findings.md     METR 2026, arXiv, OpenReview basis
└── customisation-guide.md   How to extend every layer

tests/
├── unit/
│   ├── test_task.py                    ✅ BUILT
│   ├── test_ledger.py                  ✅ BUILT
│   ├── test_circuit_breaker.py         ✅ BUILT
│   ├── test_high_impact_gaps.py        ✅ BUILT (31 checks)
│   ├── test_verifiers.py               ✅ BUILT  [Phase 2]
│   ├── test_hooks.py                   ✅ BUILT  [Phase 3]
│   ├── test_context.py                 ✅ BUILT  [Phase 3]
│   ├── test_agents.py                  ✅ BUILT  [Phase 4]
│   ├── test_skill_library.py           ✅ BUILT  [SkillLibrary] — 32 tests
│   ├── test_tracer.py                  [Phase 6] ← next
│   ├── test_storage.py                 [Phase 6] ← next
│   ├── test_entropy_gc.py              [Phase 6] ← next
│   ├── test_identity_guard.py          [Week 12]
│   ├── test_policy_engine.py           [Week 11]
│   ├── test_sla.py                     [Phase 4+]
│   ├── test_audit.py                   [Phase 5+]
│   ├── test_eval.py                    [Phase 9]
│   └── test_snapshot.py                [Phase 10]
└── integration/
    ├── test_runner.py                  ✅ BUILT — VeridianRunner + ParallelRunner full pipeline
    ├── test_full_flow.py               [Phase 5] — end-to-end with TrustedExecutor
    ├── test_protocols.py               [Phase 6+]
    ├── test_multi_agent.py             [Phase 7+]
    └── test_replay.py                  [Phase 10]

examples/
├── p6_aml_kyc_investigation/  ✅ BUILT — AML/KYC pipeline, 10 synthetic alerts, composite verifier
├── p9_crash_recovery/         ✅ BUILT — 50-task migration, simulated crash + recovery demo
├── experiments/               ✅ BUILT — SkillNet x AutoResearch experiment suite (E01-E09)
│   ├── e01_skill_trust_decay.py
│   ├── e02_static_vs_dynamic_confidence.py
│   ├── e03_semantic_grounding_retrieval.py
│   ├── e04_crossrun_consistency_drift.py
│   ├── e05_adversarial_skill_poisoning.py
│   ├── e06_trust_propagation.py
│   ├── e07_compliance_ontology.py
│   ├── e08_regulatory_amendment.py
│   └── e09_e2e_ablation.py
├── 01_legal_due_diligence/    LegalClauseVerifier + quote_match (PDF)  [Phase 7]
├── 02_codebase_migration/     MigrationVerifier + bash_exit (pytest)   [Phase 7]
├── 03_compliance_audit/       SOC2ControlVerifier + semantic_grounding  [Phase 7]
├── 04_data_pipeline_repair/   SchemaOutputVerifier + schema (JSON)      [Phase 7]
└── 05_content_moderation/     ModerationVerifier + schema (enum)        [Phase 7]
```

---

## Current Build Status

```
✅ Phase 1  core/ + ledger/ + providers/ + trusted_executor — 128 tests
✅ Phase 2  verify/builtin/ — 10 verifiers (bash, quote, schema, http, file_exists,
            composite, any_of, llm_judge, semantic_grounding, confidence)
✅ Phase 3  hooks/ + context/ — 6 built-in hooks, TokenWindow, ContextManager, Compactor
✅ Phase 4  agents/ + core/config.py — WorkerAgent, InitializerAgent, ReviewerAgent
✅ Phase 5  loop/runner.py + parallel_runner.py — 239 tests total, all passing
✅ SkillLib skills/ — Bayesian scoring, 4-gate admission, cosine dedup — 274 tests total
🔲 Phase 6  [Wk 8]     observability/ + storage/ + entropy/                   ← NEXT
🔲 Phase 7  [Wk 9-10]  cli/ + examples/
─────────────────────── v1.0.0 ─────────────────────────
🔲 Week 11             verify/policy.py + verify/templates/
🔲 Week 12             secrets/ + hooks/builtin/identity_guard.py
🔲 Phase 8+            See ROADMAP_PHASE8_PLUS.md for v1.1-v2.2 plan
─────────────────────── v2.0.0 ─────────────────────────
```

Critical path is strictly linear. No phase starts until previous passes all gates.

---

## Invariants — Never Violate

| # | Rule | Enforced in |
|---|------|-------------|
| 1 | `TaskLedger` is the ONLY object that transitions task status | `ledger/ledger.py` |
| 2 | `reset_in_progress()` is the FIRST call in every `run()` | `loop/runner.py` |
| 3 | All state writes use `temp → os.replace()` | All storage + ledger |
| 4 | Hook errors are ALWAYS caught in `HookRegistry.fire()` | `hooks/registry.py` |
| 5 | Verifiers are stateless — no instance-level mutable state | `verify/builtin/` |
| 6 | `LLMJudgeVerifier` is NEVER standalone — always in `CompositeVerifier` | `verify/builtin/composite.py` |
| 7 | `executor.set_task_id()` called BEFORE each task's commands | `loop/runner.py` |
| 8 | `ConfidenceScore` attached to EVERY `TaskResult` after `mark_done()` | `loop/runner.py` |
| 9 | `EntropyGC` NEVER mutates — `entropy_report.md` only | `entropy/gc.py` |
| 10 | Secret VALUES never reach any log, trace, or progress file | `hooks/builtin/identity_guard.py` |
| 11 | `ContextManager` assembly is frozen (6 steps, CLAUDE.md §2.4) | `context/manager.py` |
| 12 | Hook methods NEVER called directly — always via `HookRegistry.fire(event)` | All callers |
| 13 | All raises use `VeridianError` hierarchy — no bare `Exception` | All modules |
| 14 | Verifier error messages: specific + actionable + ≤ 300 chars | `verify/base.py` |
| 15 | `result.structured` never accessed before verifier runs | `loop/runner.py` |

---

## Entry Points by Task

| What you're doing | Read first | Reference impl |
|-------------------|-----------|----------------|
| Adding a verifier | `verify/base.py` | `verify/builtin/semantic_grounding.py` |
| Adding a hook | `hooks/base.py` | `hooks/builtin/cross_run_consistency.py` |
| Changing task state machine | `core/task.py` | `core/exceptions.py` |
| Changing ledger logic | `ledger/ledger.py` | `core/events.py` |
| Changing runner logic | `loop/runner.py` | `loop/trusted_executor.py` |
| Adding context logic | `context/manager.py` | `context/window.py` |
| Adding a storage backend | `storage/local_json.py` | `core/exceptions.py` |
| Adding a secrets provider | `secrets/base.py` | `secrets/env.py` |
| Adding a CLI command | `cli/main.py` | `loop/runner.py` |
| Writing an example | `examples/p6_aml_kyc_investigation/` | `verify/builtin/composite.py` |
| Querying SkillLibrary | `skills/library.py` | `skills/store.py` |
| Extracting skills post-run | `skills/extractor.py` | `skills/admission.py` |
| Understanding full flow | `docs/architecture.md` | `docs/concepts.md` |

---

## Runner Execution Sequence (Frozen)

```
VeridianRunner.run()
  1. ledger.reset_in_progress(run_id)          ← ALWAYS FIRST
  2. hooks.fire(RunStarted(...))
  3. Install SIGINT handler
  └─ LOOP while task = ledger.get_next():
      a. hooks.fire(TaskClaimed(...))
      b. ledger.claim(task.id, run_id)
      c. executor.set_task_id(task.id)          ← before any bash
      d. context = context_manager.build(task, attempt)
      e. result  = worker.run(context)
      f. verdict = verifier.verify(task, result)
      g. PASS → mark_done → attach ConfidenceScore → hooks.fire(TaskCompleted)
         FAIL → mark_failed → retry? → hooks.fire(TaskFailed)
  4. hooks.fire(RunCompleted(...))
  5. return RunSummary
```

---

## ContextManager Assembly (Frozen)

```
1. [SYSTEM]       worker.md system prompt
2. [ORIENTATION]  run summary + last 5 lines of progress.md
3. [TASK]         title + description + verifier type + required fields
4. [RETRY ERROR]  ONLY if attempt > 0 — verbatim last_error
5. [ENVIRONMENT]  context_files from metadata, only if budget allows
6. [OUTPUT FMT]   exact <veridian:result> format with field names
```

---

## TaskStatus State Machine

```
PENDING ──claim()──► IN_PROGRESS ──mark_done()──► DONE
                           │
                     mark_failed()
                           │
                        FAILED ──retry?──► PENDING
                           │
                      max retries?
                           │
                       ABANDONED
```

Only `TaskLedger` executes transitions. Any other code touching status is a bug.

---

## Extension Points

| Point | Group | ABC |
|-------|-------|-----|
| Verifiers | `veridian.verifiers` | `verify/base.py:BaseVerifier` |
| Hooks | `veridian.hooks` | `hooks/base.py:BaseHook` |
| Storage | `veridian.storage` | `storage/base.py:BaseStorage` |
| Secrets | `veridian.secrets` | `secrets/base.py:SecretsProvider` |

```toml
# pyproject.toml
[project.entry-points."veridian.verifiers"]
my_verifier = "mypackage.verifiers:MyVerifier"
```

---

## OTel Attribute Namespace

```
gen_ai.system = "veridian"
gen_ai.request.model | gen_ai.usage.input_tokens | gen_ai.usage.output_tokens
gen_ai.operation.name = "task_execution"
veridian.task.id | veridian.task.phase | veridian.task.verifier_id | veridian.task.retry_count
veridian.run.id  | veridian.run.ledger_file
```

Dashboard: `http://localhost:7474` (not 8080, not 7860).

---

## Quality Gates (All Must Pass Before Phase Exits)

```bash
ruff check . && ruff format --check .
mypy veridian/ --strict
pytest -x --tb=short
pytest --cov=veridian --cov-fail-under=85 -q
```

---

## What Not To Do

| Never | Because |
|-------|---------|
| Call LLM in a verifier (except `llm_judge`) | Verifiers must be deterministic |
| Use `LLMJudgeVerifier` standalone | Runtime enforced by CompositeVerifier |
| Let hook errors propagate | One broken hook must never kill a run |
| Add mutable state to verifiers | Must be stateless + thread-safe |
| Write state without `os.replace()` | Crash mid-write corrupts ledger |
| Instantiate deps inside `__init__` | Breaks DI, blocks MockProvider |
| Raise bare `Exception`/`ValueError` | Breaks error hierarchy |
| Auto-fix anything in `EntropyGC` | Read-only by design |
| Log secret values | `[REDACTED:secret_ref]` only |
| Access `result.structured` before verify | May be None |
| Hardcode model names | Use config or `VERIDIAN_MODEL` |
| Reorder ContextManager steps | Breaks agent context contract |
| Reorder `runner.run()` steps | Breaks crash recovery |
| Skip tests-first | Non-negotiable |
| Leave `__init__.py` without `__all__` | Breaks public API surface |


---

*AGENTS.md v2.2 | Veridian Project Map | 2026-03-24*