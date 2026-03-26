# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
