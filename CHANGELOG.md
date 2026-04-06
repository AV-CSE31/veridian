# Changelog

> **CONFIDENTIAL — DO NOT COMMIT TO GIT**

All notable changes to Veridian are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — 2026-04-07

### Breaking Changes

- **Removed 19 deprecated top-level imports.** `veridian.AdversarialEvaluator`,
  `veridian.SprintContract`, `veridian.AgentRecorder`, `veridian.ActionConfig`,
  and 15 other experimental symbols now raise `AttributeError` from `veridian.*`.
  Import from `veridian.experimental` instead. See `planning/MIGRATION_v3_to_v4.md`.
- **Reduced `veridian.__all__` from 123 to 40 symbols.** Non-core symbols (OTel,
  cost tracking, individual hook classes, quality gate internals, entropy GC,
  TrustedExecutor, BashOutput, etc.) are no longer in `__all__`. They remain
  importable from their module paths.
- **Deleted duplicate package trees** `veridian/explain/explain/` and
  `veridian/intelligence/intelligence/` (0% coverage, 0 runtime references).

### Added

- **Dead Letter Queue CLI** (`veridian dlq status|list|retry|dismiss|report`)
  for operator triage of abandoned tasks. Abandoned tasks are auto-enqueued.
- **SecretsGuard verifier** now registered in built-in registry as `secrets_guard`.
  Scans outputs for API keys, tokens, passwords, connection strings, and
  high-entropy strings.
- **Package hygiene CI test** blocks duplicate `x/x/` nested packages from
  ever recurring.
- `planning/MIGRATION_v3_to_v4.md` — complete replacement matrix for all
  removed/moved symbols.

### Fixed

- `veridian.__all__` / runtime behavior now match `planning/API_STABILITY.md`
  exactly — no more trust gap between docs and code.

---

## [Unreleased]

### Phase 7b — Evolution Safety (next)
- Evolution Monitor (6-pathway misevolution detection)
- Behavioral Fingerprinting (multi-dimensional per-run signatures)
- Sandbox isolation + canary tasks

### Added
- `veridian run --share-report` to generate `veridian_share_<run_id>.md` beside the ledger after a run
- Share reports include a "Verified with Veridian" badge, current verified and follow-up task highlights, and GitHub/PyPI CTA links
- Lightweight measurement via `share_report_generated` and `share_report_generation_failed` events in `veridian_trace.jsonl`

---

## [0.1.0] — 2026-03-29

### Added

**Phase 1 — Core + Ledger + Providers**
- `Task`, `TaskStatus` state machine, `TaskResult`, `TaskPriority`, `LedgerStats`
- 40+ typed events (`RunStarted`, `TaskCompleted`, `VerificationFailed`, etc.)
- `VeridianError` exception hierarchy (20+ exception types)
- `TaskLedger`: atomic CRUD with `os.replace()`, crash recovery, `FileLock`
- `TrustedExecutor` with 5-layer ACI injection defense, `OutputSanitizer`
- `LiteLLMProvider` with circuit breaker, retry, fallback, context guard
- `MockProvider` with script/respond_when patterns

**Phase 2 — Verifiers**
- `BaseVerifier` ABC, `VerificationResult`, `VerifierRegistry` (entry-point autodiscovery)
- 12 built-in verifiers: `bash_exit`, `schema`, `quote_match`, `http_status`, `file_exists`,
  `composite`, `any_of`, `llm_judge`, `semantic_grounding`, `self_consistency`,
  `tool_safety`, `memory_integrity`
- `CompositeVerifier` AND chain with prefixed sub-errors
- `AnyOfVerifier` OR chain
- `LLMJudgeVerifier` guard (never standalone)

**Phase 3 — Hooks + Context**
- `BaseHook` ABC with 11 lifecycle methods (all default no-op)
- `HookRegistry` with priority ordering and error isolation
- 7 built-in hooks: `LoggingHook`, `CostGuardHook`, `HumanReviewHook`,
  `RateLimitHook`, `SlackNotifyHook`, `CrossRunConsistencyHook`, `DriftDetectorHook`
- `TokenWindow`, `ContextManager` (frozen 6-step assembly), `ContextCompactor` (85% threshold)

**Phase 4 — Agents + Config**
- `VeridianConfig`: central configuration with env var support
- `WorkerAgent`: agentic loop with `<veridian:result>` parsing
- `InitializerAgent`: goal-to-task spec validation
- `ReviewerAgent`: optional post-run result review

**Phase 5 — Runner**
- `VeridianRunner`: SIGINT-safe frozen execution sequence, dry_run, `RunSummary`
- `ParallelRunner`: asyncio + semaphore, bounded concurrency

**SkillLibrary**
- `Skill`, `SkillStep`, `SkillCandidate` with Bayesian reliability scoring
- `SkillStore`: atomic JSON, cosine similarity retrieval
- `SkillExtractor`: DONE tasks to confidence-filtered skill extraction
- `SkillAdmissionControl`: 4-gate (confidence, retries, min_steps, cosine dedup)
- `SkillLibrary` facade: `post_run()`, `query()`, `record_outcome()`

**DriftDetector**
- `DriftDetectorHook`: Bayesian behavioral regression detection across runs (priority=90)
- `RunSnapshot`, `DriftSignal`, `DriftReport` data models
- Atomic JSONL persistence

**Phase 6 — Observability + Storage + Entropy**
- `VeridianTracer`: OTel GenAI v1.37+, JSONL fallback, thread-safe
- `VeridianDashboard`: FastAPI SSE on port 7474
- `BaseStorage` ABC with `LocalJSONStorage`, `RedisStorage`, `PostgresStorage`
- `EntropyGC`: 9 read-only consistency checks, atomic `entropy_report.md`

**Phase 6b — Anti-Misevolution Safety**
- `ToolSafetyVerifier`: AST-based static analysis (eval/exec, shell injection, blocked imports)
- `MemoryIntegrityVerifier`: reward hacking, prompt injection, numeric drift, contradiction detection
- `VerifierIntegrityChecker`: SHA-256 fingerprint + tamper detection

**Phase 7 — CLI**
- Typer + Rich CLI: `init`, `run`, `status`, `gc`, `reset`, `retry`, `skip`, `report`

**Extras**
- `SprintContract`: HMAC-SHA256 signed pre-execution commitment
- `@verified` decorator: deterministic verification on any function
- `AdversarialEvaluator`: GAN-inspired generator/judge separation
- `CalibrationProfile`, `EvalPipeline`
- `TestRecorder`, `TestReplayer` for deterministic replay
- `VeridianConfig.__post_init__` validation with path traversal guard
- Agent identity PKI (Ed25519), knowledge graph, policy compiler, compliance checker
- Crypto audit trail, dashboard data layer, budget tracking, cost accounting

### Infrastructure
- 741 tests (unit + integration)
- ruff + mypy strict + 85% coverage gates
- PyPI: `veridian-ai`
- GitHub Actions CI/CD with automated publish on release
- 4 example pipelines (AML/KYC, crash recovery, skill optimization, drift detection)

---

*CHANGELOG.md v1.0 | 2026-03-30*
