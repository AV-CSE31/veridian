# CLAUDE.md — Veridian Engineering Contract

> Persistent instruction set for all Claude Code sessions on this project.
> Read this file **in full** before writing a single line of code.
> When this file and any task prompt conflict, **this file wins** — pause and clarify.

---

## 0. Project Identity & Hard Constraints

**Veridian** — deterministic verification infrastructure for autonomous AI agents.
The CI/CD equivalent for agentic systems. Python ≥ 3.11. MIT License.


---

## 1. The Five Prime Directives

These override every other instruction in this file and every task prompt.
When uncertain about anything, return here first.

---

### 1.1 — Tests First. Always.

```
REQUIRED order for every implementation task:
  1. Write tests/unit/test_<module>.py  →  all new tests fail (red)
  2. Write veridian/<module>.py         →  make tests pass (green)
  3. Refactor                           →  tests stay green

FORBIDDEN:
  - Writing veridian/<module>.py before its test file exists
  - Saying "add tests later"
  - Generating implementation and test in the same pass without red→green ordering
```

If a prompt says "implement X", your **first output** is `tests/unit/test_X.py`.

---

### 1.2 — Interface Before Implementation

Every module with multiple concrete implementations defines its ABC **before** any
concrete class. ABCs are frozen contracts — method signatures do not change after a phase
ships without a formal deprecation cycle and CHANGELOG entry.

```python
# CORRECT: base.py is written and frozen before any builtin/ file is created
class BaseVerifier(ABC):
    id: ClassVar[str]

    @abstractmethod
    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Verify task result. Return VerificationResult(passed=True) or error string."""
```

---

### 1.3 — Atomic Writes for All Persistent State

Every file write that touches durable state uses temp-file + `os.replace()`. No exceptions.

```python
# CORRECT — the only acceptable pattern for all state writes
import json, os, tempfile
from pathlib import Path

def _atomic_write(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp"
    ) as f:
        json.dump(data, f, indent=2)
        tmp = Path(f.name)
    os.replace(tmp, path)  # POSIX-atomic: readers never see partial writes

# WRONG — partial write on crash corrupts the ledger permanently
with open(path, "w") as f:
    json.dump(data, f)
```

Applies to: `ledger.json`, `progress.md`, `veridian_trace.jsonl`, all storage backends,
audit chain files, snapshot files. Non-negotiable.

---

### 1.4 — Events Through Registry. Never Direct.

Hook methods are **never** called directly. Every state transition fires a typed event
through `HookRegistry.fire()`. Hook errors are **always** caught inside `fire()` and logged —
they never propagate. One broken hook never kills a run.

```python
# CORRECT
self.hooks.fire(TaskCompleted(task_id=task.id, result=result, run_id=self.run_id))

# WRONG — bypasses error isolation, one failing hook crashes the entire run
for hook in self.hooks:
    hook.after_result(task, result)
```

---

### 1.5 — Raise From the Hierarchy. Never Bare.

```python
# CORRECT
from veridian.core.exceptions import BlockedCommand
raise BlockedCommand(f"Command '{cmd}' is in blocklist. Safe alternatives: ls, cat, echo.")

# WRONG — every one of these is a bug
raise ValueError("...")
raise Exception("...")
raise RuntimeError("...")
raise TypeError("...")
```

If a genuinely new error type is needed: add it to `veridian/core/exceptions.py` **first**,
in the same commit, before any code that raises it.

---

## 2. Architecture Constraints

### 2.1 — Dependency Injection Everywhere

Classes never instantiate their own dependencies. All major dependencies are injected
through the constructor. The test: if you cannot swap in `MockProvider` without touching
production code, the design is wrong.

```python
# CORRECT — fully injectable, fully testable
class VeridianRunner:
    def __init__(
        self,
        ledger: TaskLedger,
        provider: BaseProvider,        # MockProvider drops in here
        verifier: BaseVerifier,
        hooks: HookRegistry,
        executor: TrustedExecutor,
        config: VeridianConfig,
    ) -> None: ...

# WRONG — hard dependency, integration test requires real LLM
class VeridianRunner:
    def __init__(self, config: VeridianConfig) -> None:
        self.provider = LiteLLMProvider(config.model)   # ← untestable
```

### 2.2 — Entry-Point Autodiscovery for Every Extension Point

Never hardcode a plugin registry. All four extension points use `importlib.metadata`.

| Extension Point   | Entry-Point Group       | Reference ABC                     |
|-------------------|-------------------------|-----------------------------------|
| Verifiers         | `veridian.verifiers`    | `verify/base.py:BaseVerifier`     |
| Hooks             | `veridian.hooks`        | `hooks/base.py:BaseHook`          |
| Storage backends  | `veridian.storage`      | `storage/base.py:BaseStorage`     |
| Secrets providers | `veridian.secrets`      | `secrets/base.py:SecretsProvider` |

```python
# Autodiscovery pattern — identical for all four registries
from importlib.metadata import entry_points

def _autodiscover(self) -> None:
    for ep in entry_points(group="veridian.verifiers"):
        cls = ep.load()
        self._registry[cls.id] = cls
```

```toml
# pyproject.toml — built-in verifiers declared here
[project.entry-points."veridian.verifiers"]
bash_exit    = "veridian.verify.builtin.bash:BashExitCodeVerifier"
quote_match  = "veridian.verify.builtin.quote:QuoteMatchVerifier"
schema       = "veridian.verify.builtin.schema:SchemaVerifier"
http_status  = "veridian.verify.builtin.http:HttpStatusVerifier"
file_exists  = "veridian.verify.builtin.file_exists:FileExistsVerifier"
composite    = "veridian.verify.builtin.composite:CompositeVerifier"
any_of       = "veridian.verify.builtin.any_of:AnyOfVerifier"
llm_judge    = "veridian.verify.builtin.llm_judge:LLMJudgeVerifier"
```

### 2.3 — Explicit `__all__` in Every Public `__init__.py`

No star imports. No empty `__init__.py` for public packages.

```python
# CORRECT — veridian/verify/__init__.py
from veridian.verify.base import BaseVerifier, VerificationResult, VerifierRegistry

__all__ = ["BaseVerifier", "VerificationResult", "VerifierRegistry"]

# WRONG
from veridian.verify.base import *    # never
# (empty __init__.py for a public package)  # never
```

### 2.4 — ContextManager Assembly Order is a Frozen Specification

`ContextManager.build_worker_context()` assembles the prompt in exactly this order.
Do not reorder. This is a contract the WorkerAgent prompt engineering depends on.

```
1. [SYSTEM]       worker.md system prompt            — always included, never compacted
2. [ORIENTATION]  run summary + last 5 lines of progress.md
3. [TASK]         title, description, verifier_id, required_fields
4. [RETRY ERROR]  verbatim last_error (≤ 300 chars) — ONLY if attempt > 0
5. [ENVIRONMENT]  context_files from task.metadata  — ONLY if token budget allows
6. [OUTPUT FMT]   exact <veridian:result> XML format with required field names
```

Compaction at 85% budget never touches: system prompt, last 3 exchanges, current task block.

### 2.5 — EntropyGC is Permanently Read-Only

`EntropyGC` reads, detects, and writes `entropy_report.md` only.
It **never** calls any mutating method on `TaskLedger` or any other stateful object.

```python
# CORRECT — detect and return, never fix
def check_stale_in_progress(self) -> list[EntropyIssue]:
    return [
        EntropyIssue(type="stale_in_progress", task_id=t.id, detail="...")
        for t in self.ledger.get_by_status(TaskStatus.IN_PROGRESS)
        if self._is_stale(t)
    ]

# WRONG — EntropyGC never calls ANY mutating ledger method
def check_stale_in_progress(self) -> None:
    for t in ...:
        self.ledger.reset(t.id)   # NEVER
```

### 2.6 — LLMJudgeVerifier is Never Standalone

Enforced at `CompositeVerifier.__init__()`. If `llm_judge` is the only verifier, raise
immediately. LLM judgment is probabilistic — it must be gated by a deterministic check.

```python
# Required guard in CompositeVerifier.__init__
if len(self.verifiers) == 1 and self.verifiers[0].id == "llm_judge":
    raise VeridianConfigError(
        "LLMJudgeVerifier cannot run standalone. "
        "Wrap it with at least one deterministic verifier in CompositeVerifier."
    )
```

### 2.7 — Secret Values Never Touch Any Output Surface

`IdentityGuard` scrubs every secret value before anything reaches `progress.md`,
`veridian_trace.jsonl`, stdout, stderr, or any hook payload.

```python
# Audit log entry — ref only, never value
{"event": "secret_accessed", "task_id": "task_42",
 "secret_ref": "prod/db-creds", "timestamp": "2026-03-21T10:00:00Z"}

# Scrubbed output surface
"stdout": "Connected to [REDACTED:prod/db-creds] successfully"

# NEVER — in any log, trace, progress.md, or hook payload
"stdout": "Connected to postgres://user:s3cr3t@host/db"
```

`rotate_check()` is called on **every** `before_task` — not once per run.

---

## 3. Code Quality — All Gates Are Blocking

Generate code that passes all five gates on first generation.
A phase is not done until all five exit 0.

### 3.1 — Ruff

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM", "TCH", "RUF"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]
```

### 3.2 — Mypy (strict)

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_any_generics = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

Every public function: fully typed. No `Any` without an inline justification comment.

### 3.3 — Coverage

```toml
[tool.pytest.ini_options]
addopts = "--cov=veridian --cov-report=term-missing --cov-fail-under=85"
```

### 3.4 — Full Gate Run (in this order)

```bash
ruff check .
ruff format --check .
mypy veridian/ --strict
pytest -x --tb=short -q
pytest --cov=veridian --cov-fail-under=85
```

---

## 4. Testing Patterns

### 4.1 — Standard Unit Test Structure

```python
# tests/unit/test_<module>.py
import pytest
from veridian.<module> import MyClass
from veridian.providers.mock_provider import MockProvider
from veridian.core.exceptions import SomeVeridianError


class TestMyClass:
    @pytest.fixture
    def subject(self) -> MyClass:
        """Always use fixtures. Never instantiate in test body."""
        return MyClass(provider=MockProvider())

    def test_happy_path(self, subject: MyClass) -> None:
        """Should <do X> when <condition Y>."""
        result = subject.method(valid_input)
        assert result.field == expected

    def test_raises_specific_error(self, subject: MyClass) -> None:
        """Should raise SomeVeridianError when input is invalid."""
        with pytest.raises(SomeVeridianError, match="fragment of expected message"):
            subject.method(invalid_input)

    def test_edge_case(self, subject: MyClass) -> None:
        """Should handle <edge case> gracefully."""
        ...
```

**Minimum per module:** 2 tests per public method (pass + fail) + edge cases for all IO/parsing paths.

### 4.2 — Verifier Test Pattern (exactly four per verifier)

```python
class TestBashExitCodeVerifier:
    def test_passes_when_exit_code_matches(self): ...
    def test_fails_when_exit_code_mismatches(self): ...
    def test_error_message_is_actionable(self):
        result = verifier.verify(task, bad_result)
        assert "exit" in result.error.lower()    # names what failed
        assert len(result.error) <= 300           # within agent retry budget
    def test_config_validation_rejects_negative_timeout(self): ...
```

### 4.3 — Integration Test Pattern

```python
def test_full_runner_happy_path(tmp_path: Path) -> None:
    """Full pipeline: task → execution → verification → DONE."""
    provider = MockProvider()
    provider.script([
        MockResponse(content='<veridian:result>{"summary": "done"}</veridian:result>'),
    ])
    ledger = TaskLedger(ledger_file=tmp_path / "ledger.json")
    ledger.add(Task(id="t1", title="Test", verifier_id="schema", ...))

    runner = VeridianRunner(ledger=ledger, provider=provider, ...)
    summary = runner.run()

    assert summary.done_count == 1
    assert summary.failed_count == 0
    assert ledger.get("t1").status == TaskStatus.DONE
```

### 4.4 — Hook Isolation Test (mandatory for any hook test file)

```python
def test_broken_hook_never_kills_run() -> None:
    """Hook exceptions must be swallowed by HookRegistry. Run continues."""
    class BrokenHook(BaseHook):
        id = "broken"
        def before_task(self, event: TaskClaimed) -> None:
            raise RuntimeError("hook exploded")

    registry = HookRegistry()
    registry.register(BrokenHook())
    registry.fire(TaskClaimed(task_id="t1", run_id="r1"))  # must not raise
```

### 4.5 — Atomic Write Test (mandatory for any storage test file)

```python
def test_no_partial_write_on_concurrent_access(tmp_path: Path) -> None:
    """Ledger file must never be readable in a partial state."""
    ledger = TaskLedger(ledger_file=tmp_path / "ledger.json")
    ledger.add(Task(id="t1", ...))
    ledger.add(Task(id="t2", ...))
    assert (tmp_path / "ledger.json").exists()
    assert not list(tmp_path.glob("*.tmp"))  # no temp files left behind
```

---

## 5. Phase Work Protocol

### 5.1 — Start-of-Phase Sequence (follow exactly)

```
1.  Read the phase spec section in full from the feature strategy doc
2.  List every file to be created (from spec — do not invent files)
3.  Run: pytest -x -q   (existing 31 tests must be green before you start)
4.  Generate ALL test files for the phase (they fail — that's correct)
5.  Implement ABCs and base classes
6.  Implement concrete classes / builtins
7.  Make all tests pass
8.  Update all __init__.py files with __all__
9.  Update pyproject.toml entry-points (if new extension points added)
10. Run all 5 quality gates
11. Update CHANGELOG.md
```

**Never mix phases.** If implementing Phase 3 (Hooks + Context), do not create or modify
Phase 4 (Agents) files — not even stub files.

### 5.2 — Phase Exit Checklist

```
[ ] All new tests passing
[ ] All 31 pre-existing tests still passing
[ ] ruff check .              exits 0
[ ] ruff format --check .     exits 0
[ ] mypy veridian/ --strict   exits 0
[ ] pytest --cov-fail-under=85 exits 0
[ ] Every new __init__.py has __all__
[ ] pyproject.toml entry-points updated (if applicable)
[ ] CHANGELOG.md entry written
[ ] Zero files from future phases touched
```

### 5.3 — Build Order (Critical Path)

```
Phase 2  [Wk 1-2]   verify/builtin/ — 10 verifiers            ✅ DONE
Phase 3  [Wk 3-4]   hooks/ + context/                         ✅ DONE
Phase 4  [Wk 5]     agents/ + core/config.py                  ✅ DONE
Phase 5  [Wk 6-7]   loop/runner.py + loop/parallel_runner.py  ✅ DONE
SkillLib [post-5]   skills/ + Bayesian scoring + examples      ✅ DONE
Phase 6  [Wk 8]     observability/ + storage/ + entropy/       🔲 NEXT
Phase 7  [Wk 9-10]  cli/ + examples/                          🔲
─── v1.0.0 ────────────────────────────────────────────────────────
Phase 2+ [Wk 11]    verify/policy.py + templates/             🔲
Phase 3+ [Wk 12]    secrets/ + hooks/builtin/identity_guard   🔲
Phase 8+ [Month 4+] See ROADMAP_PHASE8_PLUS.md                🔲
```

No strategic features (MCP, A2A, multi-agent) until Phase 7 exits clean.

---

## 6. Module-Specific Rules

### `veridian/ledger/ledger.py`
- `reset_in_progress(run_id)` is **always** the first call in `VeridianRunner.run()`. Never move it.
- Every mutation: `FileLock` → modify in memory → `_atomic_write()`. No bare file writes.
- `get_next()` **never** returns a task whose `depends_on` entries are not all `DONE`.
- `TaskLedger` is the **only** object permitted to transition task status.
  Any other code setting `task.status` directly is a bug.

### `veridian/verify/`
- Verifier `id` is `ClassVar[str]` — class-level, immutable, never set on instance.
- `VerificationResult.error` is shown verbatim to the agent as `[RETRY ERROR]`.
  Write it as a senior engineer: name the field, state what was wrong, state the fix. ≤ 300 chars.
- `CompositeVerifier` prefixes sub-errors: `"[Step 2/3] schema: field 'risk_level' must be LOW|MEDIUM|HIGH|CRITICAL, got 'unknown'"`.
- Verifiers are **stateless**. No instance-level mutable fields. Concurrent-use safe.

### `veridian/hooks/`
- Hook `priority` is `ClassVar[int]`. Lower = runs earlier.
  Built-in priorities: `logging_hook=0`, `identity_guard=5`, all others=50.
- `HookRegistry.fire()` iterates in ascending priority order.
- `BaseHook` all methods default to no-op. Subclasses override only what they need.
- `HookRegistry.fire()` wraps every hook call in `try/except Exception`, logs failure, never re-raises.

### `veridian/loop/runner.py`
- Runner sequence is frozen (see AGENTS.md § "Task Execution Flow"). Never reorder.
- SIGINT: set `_shutdown` flag. After **current task** completes, write `RunSummary`, exit cleanly.
  Never `sys.exit()` mid-task.
- `dry_run=True`: assemble context, log what would execute, return `RunSummary(dry_run=True)`.
  Never call `provider.complete()`.
- `ConfidenceScore.compute()` is called and attached to `TaskResult` after every `mark_done()`.
  Skipping this is a bug.

### `veridian/observability/`
- OTel attributes: `gen_ai.*` for GenAI Semantic Conventions v1.37+, `veridian.*` for project-specific.
- JSONL fallback: if OTel export fails, append to `veridian_trace.jsonl`. **Never lose a trace event.**
- Dashboard port: **7474**. Not 8080. Not 7860. This is in config default and all docs.

### `veridian/storage/`
- All three backends implement the identical `BaseStorage` interface. No backend-specific callers.
- `LocalJSONStorage`: zero external deps beyond stdlib + `filelock`. This is the default backend.
- `RedisStorage.get_next()`: sorted set keyed by priority. `SETNX` for distributed lock.
- `PostgresStorage.get_next()`: `SELECT ... FOR UPDATE SKIP LOCKED`. Auto-migrate on `__init__()`.

### `veridian/cli/main.py`
- All output via `rich`. No bare `print()` anywhere in `cli/`.
- Every command has a meaningful `--help` description.
- Destructive commands (`reset`, `skip`, `retry`): require `--confirm` or `Confirm.ask()`.
- `veridian gc` maps to `EntropyGC.run()` — report only, never mutates. The CLI must not add mutation.

### `veridian/secrets/`
- `EnvSecretsProvider`: the only provider usable in CI without external services.
- All other providers: raise `ProviderError` gracefully on absent credentials. Never crash.
- `rotate_check()` on every `before_task`, not once per run. Credentials rotate mid-run.
- Scrubbed placeholder format: `[REDACTED:<secret_ref>]`. Consistent everywhere.

### `veridian/agents/worker.py`
- Result regex: `re.compile(r"<veridian:result>\s*(\{.*?\})\s*</veridian:result>", re.DOTALL)`
- Loop exits on: result found, OR `len(messages) > config.max_turns_per_task`.
- No result and no bash commands: append `{"role": "user", "content": "Output a <veridian:result> block now."}`.
- Never hardcode `max_turns`. Always read from `VeridianConfig.max_turns_per_task`.

---

## 7. Hard Stops

If a task prompt implies any of these, **stop and clarify** before writing code.

| # | Never Do | Why |
|---|----------|-----|
| 2 | Raise bare `Exception` / `ValueError` / `RuntimeError` | Breaks error hierarchy |
| 3 | Write state without `os.replace()` | Crash = corrupted ledger |
| 4 | Call hook method directly | One failure kills the run |
| 5 | `LLMJudgeVerifier` standalone | Probabilistic gate without deterministic backstop |
| 6 | Log a secret value anywhere | Security violation, compliance failure |
| 7 | EntropyGC mutating any state | Violates read-only design invariant |
| 8 | Write implementation before test file | Breaks TDD |
| 9 | Empty `__init__.py` for public package | Breaks API surface |
| 10 | Instantiate dependency inside `__init__` | MockProvider injection impossible |
| 11 | Reorder `ContextManager` assembly | Breaks agent context contract silently |
| 12 | Reorder `VeridianRunner.run()` steps | Breaks crash recovery guarantee |
| 13 | Touch future-phase files | Phase isolation is a quality gate |
| 14 | Mutate task status outside `ledger.py` | Only TaskLedger owns status transitions |
| 15 | Hardcode model names | Use `VeridianConfig` or `VERIDIAN_MODEL` env var |

---

## 8. New File Checklist

Before submitting any new `veridian/*.py`:

```
[ ] Module docstring: one sentence, what this module does
[ ] Imports sorted (ruff --fix)
[ ] ClassVar annotations for id, priority, version fields
[ ] __all__ defined (public modules)
[ ] Every ABC @abstractmethod has a docstring
[ ] Every public method: fully type-annotated + one-line docstring
[ ] No bare except: — always except SpecificVeridianError:
[ ] No mutable default arguments
[ ] Corresponding test file written first (TDD) or already exists
[ ] ruff + mypy pass on this file in isolation before opening PR
```

---

## 9. Commit Convention

```
<type>(<scope>): <imperative subject ≤ 72 chars>

<body: what changed and WHY — not how>

<footer: BREAKING CHANGE: ..., Closes #N>
```

**Types:** `feat` `fix` `test` `refactor` `docs` `chore` `perf`

**Scopes:** `core` `ledger` `verify` `hooks` `context` `agents` `loop`
`providers` `storage` `observability` `entropy` `secrets` `protocols` `eval` `cli`

```bash
# Examples
feat(verify): add BashExitCodeVerifier with configurable timeout
test(hooks): add HookRegistry error isolation test suite
feat(loop): implement VeridianRunner with SIGINT-safe task loop

# Breaking change footer
BREAKING CHANGE: VeridianConfig.ledger_path renamed to ledger_file
```

---

## 10. Current Build State

**v0.1.0 — Built and passing (274 tests, Phases 2–5 + SkillLibrary complete):**
```
Phase 1 — Core + Ledger + Providers (✅ pre-existing):
  core/task.py              Task, TaskStatus state machine, TaskResult, TaskPriority, LedgerStats
  core/events.py            40+ typed events (RunStarted, TaskCompleted, VerificationFailed, …)
  core/exceptions.py        VeridianError hierarchy (InvalidTransition, BlockedCommand, …)
  core/quality_gate.py      TaskQualityGate (5-axis), TaskGraph (cycle detection, topo sort)
  ledger/ledger.py          TaskLedger: add/claim/submit_result/mark_done/mark_failed/get_next
  loop/trusted_executor.py  TrustedExecutor (blocklist), OutputSanitizer (5 layers), BashOutput
  providers/litellm_provider.py   CircuitBreaker → retry → fallback → context guard
  providers/mock_provider.py      script/script_text/script_veridian_result/respond_when
  agents/prompts/worker.md        WorkerAgent system prompt
  tests/unit/test_task.py         Task + TaskStatus state machine tests (27)
  tests/unit/test_ledger.py       Ledger CRUD + atomic writes + crash recovery (28)
  tests/unit/test_circuit_breaker.py   CircuitBreaker state transitions (17)
  tests/unit/test_high_impact_gaps.py  Cross-module integration checks (56)

Phase 2 — Verifiers (✅ complete):
  verify/base.py                    BaseVerifier ABC, VerificationResult, VerifierRegistry
  verify/builtin/__init__.py        Auto-registers all 10 verifiers via entry-points
  verify/builtin/bash.py            BashExitCodeVerifier — run command, check exit code
  verify/builtin/quote.py           QuoteMatchVerifier — verbatim quote in PDF/txt/md/docx
  verify/builtin/schema.py          SchemaVerifier — Pydantic or JSON Schema validation
  verify/builtin/http.py            HttpStatusVerifier — HTTP request + status/body check
  verify/builtin/file_exists.py     FileExistsVerifier — file presence, size, content checks
  verify/builtin/composite.py       CompositeVerifier — AND chain, prefixed sub-errors
  verify/builtin/any_of.py          AnyOfVerifier — OR chain, first pass wins
  verify/builtin/llm_judge.py       LLMJudgeVerifier — ALWAYS inside CompositeVerifier
  verify/builtin/semantic_grounding.py  3 hallucination classes: cross-field, range, drift
  verify/builtin/confidence.py      ConfidenceScore, SelfConsistencyVerifier
  tests/unit/test_verifiers.py      Verifier suite: pass/fail/error/config (46)

Phase 3 — Hooks + Context (✅ complete):
  hooks/base.py                         BaseHook ABC — 11 lifecycle methods, all no-op default
  hooks/registry.py                     HookRegistry — fire/register, priority order, errors caught
  hooks/__init__.py                     Public API: BaseHook, HookRegistry, all builtins
  hooks/builtin/__init__.py             Exports all 6 built-in hooks
  hooks/builtin/logging_hook.py         priority=0, structured JSON logging on every event
  hooks/builtin/cost_guard.py           Token→USD tracking, raises CostLimitExceeded at limit
  hooks/builtin/human_review.py         Pauses run, raises HumanReviewRequired on trigger criteria
  hooks/builtin/rate_limit.py           Sliding window tasks/hour + inter-task delay (sleeps only)
  hooks/builtin/slack.py                httpx POST to webhook, silent on missing webhook_url
  hooks/builtin/cross_run_consistency.py  Claim monitoring + contradiction detection across tasks
  context/window.py                     TokenWindow — budget/used/fits/consume/remaining_chars
  context/manager.py                    ContextManager — frozen 6-step assembly (§2.4 contract)
  context/compactor.py                  ContextCompactor — 85% threshold, preserves last 3 exchanges
  context/__init__.py                   Public API: ContextManager, ContextCompactor, TokenWindow
  tests/unit/test_hooks.py              Hook lifecycle, priority order, error isolation (26)
  tests/unit/test_context.py            Window budget, compaction, assembly order (19)

Phase 4 — Agents + Config (✅ complete):
  core/config.py                        VeridianConfig: model, max_turns, dry_run, ledger_file, …
  core/__init__.py (updated)            Exports VeridianConfig
  agents/base.py                        BaseAgent ABC
  agents/worker.py                      WorkerAgent — agentic loop, <veridian:result> parsing
  agents/initializer.py                 InitializerAgent — goal → task spec validation
  agents/reviewer.py                    ReviewerAgent — optional post-run result review
  agents/__init__.py                    Public API: BaseAgent, WorkerAgent, InitializerAgent, ReviewerAgent
  tests/unit/test_agents.py             Agent loop, result parsing, max_turns exit (13)

Phase 5 — Runner (✅ complete):
  loop/runner.py            VeridianRunner — SIGINT-safe frozen sequence, dry_run, RunSummary
  loop/parallel_runner.py   ParallelRunner — asyncio + semaphore, bounded concurrency
  loop/__init__.py (updated)  Exports VeridianRunner, ParallelRunner, RunSummary
  tests/integration/test_runner.py  Full pipeline: task→execute→verify→DONE + parallel (10)

SkillLibrary (✅ complete):
  skills/models.py              Skill, SkillStep, SkillCandidate, Bayesian reliability scoring
  skills/store.py               SkillStore: atomic JSON, cosine similarity retrieval, ranked by bayesian_lower_bound
  skills/extractor.py           SkillExtractor: DONE tasks → confidence filter → skill extraction
  skills/admission.py           SkillAdmissionControl: 4-gate (confidence, retries, min_steps, cosine dedup)
  skills/library.py             SkillLibrary facade: post_run(), query(), record_outcome(), export/import
  skills/__init__.py            Public API exports
  skills/prompts/extract.md     LLM prompt for skill extraction
  skills/prompts/reuse.md       LLM prompt for skill reuse injection
  tests/unit/test_skill_library.py  32 tests covering all components

  Modified for integration:
  core/config.py (added)        skill_library_path, skill_min_confidence, skill_max_retries, skill_top_k
  loop/runner.py (modified)     Wires post_run() after run completion
  agents/initializer.py (mod)   Injects verified_procedures into task.metadata

Examples (✅ complete):
  examples/p6_aml_kyc_investigation/   AML/KYC investigation pipeline with 10 synthetic alerts
  examples/p9_crash_recovery/           50-task migration with simulated crash and recovery
  examples/skill-optimization/          SkillNet x AutoResearch experiment suite (E01-E09)
```

**Next — start Phase 6 here:**
```
observability/tracer.py       VeridianTracer (OTel GenAI v1.37+, JSONL fallback)
observability/dashboard.py    FastAPI SSE dashboard — port 7474
storage/base.py               BaseStorage ABC
storage/local_json.py         LocalJSONStorage (atomic + FileLock, zero extra deps)
storage/redis_backend.py      RedisStorage (SETNX lock, sorted set get_next)
storage/postgres_backend.py   PostgresStorage (SKIP LOCKED, auto-migrate on __init__)
entropy/gc.py                 EntropyGC (read-only, 9 consistency checks, entropy_report.md)
tests/unit/test_tracer.py
tests/unit/test_storage.py
tests/unit/test_entropy_gc.py
```

**Post-v1.0 planning:**
```
ROADMAP_PHASE8_PLUS.md        Phase 8-17 strategic plan (v1.1-v2.2)
                              MCP Skill Server, tiered memory, provenance chain,
                              federation, policy engine, multi-agent orchestration
```

---

*CLAUDE.md v2.3 | Veridian Engineering Contract | 2026-03-24*
*Lives at repo root. Update when architectural decisions change.*
*Companion: AGENTS.md (layout, flows, invariants, reference tables)*