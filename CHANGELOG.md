# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Adversarial Evaluator Pipeline (feature/adversarial-evaluator)

GAN-inspired structural separation of generator and judge for reliable AI agent
output verification. Research basis: Anthropic harness design (March 2026) —
self-evaluation fails ~95% of the time; adversarial tension drives quality upward.

- `veridian/eval/sprint_contract.py` — `SprintContract`: pre-execution commitment
  between generator and evaluator; deliverables + success criteria + test conditions;
  dual-signing API (`sign_generator()` / `sign_evaluator()` / `is_signed()`);
  `to_dict()` / `from_dict()` for provenance chain; validates threshold and
  non-empty deliverables at construction time.

- `veridian/eval/calibration.py` — `RubricCriterion`, `GradingRubric`,
  `CalibrationProfile`; weighted evaluation rubric (weights must sum to 1.0,
  validated eagerly at construction time); skepticism level (0.0–1.0);
  `compute_weighted_score()` recomputes from rubric weights — never trusts LLM
  arithmetic; `CalibrationProfile.default()` for balanced general-purpose eval.

- `veridian/eval/adversarial.py` — `AdversarialEvaluator(BaseAgent)`: structurally
  separate from generator (independent `LLMProvider`); `<veridian:eval>` XML block
  parsing; per-criterion scoring; `EvaluationResult` with pass/fail, score,
  criterion_scores, specific failure citations, actionable feedback (≤ 2000 chars),
  and iteration number; raises `EvaluationError` on malformed LLM response.

- `veridian/eval/pipeline.py` — `VerificationPipeline`: enforces signed contract
  before execution; iterative evaluate loop up to `max_iterations`; returns on
  first passing evaluation; `PipelineResult` with convergence flag, iteration count,
  full `eval_history`, `best_score`, `final_eval`; fires lifecycle events
  (`EvaluationStarted`, `EvaluationCompleted`, `EvaluationConverged`,
  `EvaluationExhausted`) through optional `HookRegistry`.

- `veridian/eval/prompts/evaluator.md` — adversarial evaluator system prompt.

- `veridian/eval/__init__.py` — public API with `__all__`.

- `veridian/core/exceptions.py` — added `EvaluationError`, `ContractViolation`
  (captures `contract_id` + `reason`), `CalibrationError`; also added
  `StorageError`, `StorageLockError`, `StorageConnectionError`, `EntropyError`,
  `TracerError` (Phase 6 placeholder exceptions).

- `veridian/core/events.py` — added `ContractNegotiated`, `EvaluationStarted`,
  `EvaluationCompleted`, `EvaluationConverged`, `EvaluationExhausted` events.

- `tests/unit/test_adversarial_eval.py` — 37 tests covering `SprintContract`,
  `CalibrationProfile`, `GradingRubric`, `AdversarialEvaluator`, `VerificationPipeline`,
  `EvaluationResult`, and exception hierarchy.

### Added — Phase 6: Observability, Storage Backends & Entropy GC

#### Observability
- `veridian/observability/tracer.py` — `VeridianTracer` with OTel GenAI v1.37+ tracing and
  JSONL fallback; `TraceEvent` dataclass; `_get_otel_tracer()` helper; thread-safe atomic
  JSONL writes via `threading.Lock` + temp-file + `os.replace()`.
- `veridian/observability/dashboard.py` — `VeridianDashboard` FastAPI SSE dashboard on port
  7474 (optional `[dashboard]` extra); lazy `app` property; SSE `/events` endpoint tailing
  JSONL; `/health` and root endpoints; `serve()` via uvicorn.
- `veridian/observability/__init__.py` — re-exports `TraceEvent`, `VeridianTracer`,
  `VeridianDashboard`, `DASHBOARD_PORT`.

#### Storage Backends
- `veridian/storage/base.py` — `BaseStorage` ABC with abstract methods: `put`, `get`,
  `get_next`, `complete`, `fail`, `list_all`, `stats`.
- `veridian/storage/local_json.py` — `LocalJSONStorage`: file-backed storage, zero extra
  deps beyond stdlib + filelock; atomic writes; `FileLock` for cross-process safety;
  `get_next()` returns highest-priority PENDING task with all deps DONE.
- `veridian/storage/redis_backend.py` — `RedisStorage`: optional `[redis]` extra; SETNX
  distributed lock; sorted-set priority queue; `ZREVRANGE` candidate iteration.
- `veridian/storage/postgres_backend.py` — `PostgresStorage`: optional psycopg2 dep;
  auto-migration on `__init__`; `SELECT … FOR UPDATE SKIP LOCKED`; upsert via
  `ON CONFLICT (id) DO UPDATE`.
- `veridian/storage/__init__.py` — re-exports `BaseStorage`, `LocalJSONStorage`.
- `pyproject.toml` — `[project.entry-points."veridian.storage"]` entry-point for
  auto-discovery; `local_json` registered to `LocalJSONStorage`.

#### Entropy GC
- `veridian/entropy/gc.py` — `EntropyGC`: read-only ledger consistency checker running 9
  checks (`stale_in_progress`, `orphaned_dependency`, `circular_dependency`,
  `abandoned_blocks_pending`, `missing_required_field`, `priority_outlier`,
  `retry_exhaustion`, `duplicate_task_id`, `progress_stall`); DFS cycle detection with
  WHITE/GREY/BLACK colouring; atomic `entropy_report.md` write; **NEVER mutates state**.
- `veridian/entropy/__init__.py` — re-exports `EntropyGC`, `EntropyIssue`, `IssueType`.

#### Exceptions
- `veridian/core/exceptions.py` — added `StorageError`, `StorageLockError`,
  `StorageConnectionError`, `EntropyError`, `TracerError`.

#### Tests (TDD — written before implementation)
- `tests/unit/test_tracer.py` — 30+ tests covering JSONL fallback, thread safety, OTel
  integration, `TraceEvent.to_dict()`, `VeridianDashboard`, and edge paths.
- `tests/unit/test_storage.py` — 40+ tests covering `BaseStorage` ABC, `LocalJSONStorage`,
  `RedisStorage` (mocked), `PostgresStorage` (mocked), and entry-point autodiscovery.
- `tests/unit/test_entropy_gc.py` — 50+ tests covering all 9 checks, immutability
  invariant (no ledger mutation), `EntropyIssue`, `IssueType`, and `run()`.

#### Quality
- All 408 tests pass (was 298 before Phase 6).
- Coverage at 85.00% (gate: ≥ 85%).
- `ruff check` — no new violations introduced.
- `mypy --strict` — no issues in 67 source files.

## [0.1.0] — 2026-03-26

### Added
- Initial release: deterministic verification for AI agents.
- `TaskLedger` with crash-safe atomic JSON storage.
- `VerificationResult` + built-in verifiers: `BashExitCodeVerifier`, `QuoteMatchVerifier`,
  `SchemaVerifier`, `HttpStatusVerifier`, `FileExistsVerifier`, `CompositeVerifier`,
  `AnyOfVerifier`, `LLMJudgeVerifier`, `SemanticGroundingVerifier`,
  `SelfConsistencyVerifier`.
- `AgentRunner` with retry logic, hook system, and circuit breaker.
- Built-in hooks: `CostGuardHook`, `HumanReviewHook`, `DriftDetectorHook`,
  `RateLimitHook`, `SlackNotifyHook`, `CrossRunConsistencyHook`.
- CLI (`veridian`) via Typer + Rich.
- Entry-point autodiscovery for verifiers, hooks, and storage backends.
