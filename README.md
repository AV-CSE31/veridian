# Veridian

**Deterministic verification infrastructure for autonomous AI agents.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/AV-CSE31/veridian/actions/workflows/test.yml/badge.svg)](https://github.com/AV-CSE31/veridian/actions)
[![274 tests](https://img.shields.io/badge/tests-274%20passing-brightgreen.svg)]()

---

Every agent framework gives you a loop. Veridian gives you a **guarantee**.

```python
from veridian import TaskLedger, Task, VeridianRunner, LiteLLMProvider

ledger = TaskLedger("ledger.json")
ledger.add([
    Task(
        title="Migrate auth.py to Python 3.11",
        description="Migrate src/auth.py to Python 3.11 syntax. Verify: pytest passes.",
        verifier_id="bash_exit",
        verifier_config={"command": "pytest tests/test_auth.py -v"},
    )
])

summary = VeridianRunner(ledger=ledger, provider=LiteLLMProvider()).run()
# Kill it at any point. Re-run. It picks up exactly where it left off.
```

---

## The problem

Long-running AI agents fail not because models are incapable, but because infrastructure is missing:

| Failure mode | What happens | Veridian solution |
|---|---|---|
| Agents self-certify completion | Agent says "done" — system believes it | `BaseVerifier` — deterministic Python checks, never LLM |
| State lost on crash | Process kill at step 47/100 = start over | `TaskLedger` — atomic writes via `os.replace()`, auto-recovery |
| Context windows fill silently | Agents hallucinate as context degrades | `ContextCompactor` — 85% threshold, preserves critical context |
| Contradictions go undetected | Task 3: risk LOW. Task 47: risk CRITICAL | `CrossRunConsistencyHook` — checks claims across all tasks |
| Tool output trusted blindly | Injected instructions execute unchecked | `TrustedExecutor` — 5-layer ACI injection defense |

---

## Architecture

```
                        ┌──────────────────────────────────────────┐
                        │            CLI / Public API               │
                        │     veridian init · run · status · gc     │
                        └──────────────────┬───────────────────────┘
                                           │
                        ┌──────────────────▼───────────────────────┐
                        │              Runner Layer                 │
                        │  VeridianRunner · ParallelRunner (async)  │
                        │  SIGINT-safe · dry_run · RunSummary       │
                        └──┬──────────┬──────────┬─────────────────┘
                           │          │          │
              ┌────────────▼──┐  ┌────▼─────┐  ┌▼──────────────────┐
              │    Agents     │  │ Context  │  │  Hooks (middleware) │
              │               │  │          │  │                    │
              │ Initializer   │  │ Manager  │  │ CostGuard          │
              │ Worker        │  │ Compactor│  │ HumanReview        │
              │ Reviewer      │  │ Window   │  │ RateLimit · Slack  │
              └──────┬────────┘  └──────────┘  │ CrossRunConsistency│
                     │                          └───────────────────┘
              ┌──────▼───────────────────────────────────────────────┐
              │              Verification Layer                       │
              │                                                      │
              │  BaseVerifier ABC + VerifierRegistry (entry-points)   │
              │                                                      │
              │  bash_exit · schema · quote_match · http_status       │
              │  file_exists · composite · any_of · semantic_grounding│
              │  self_consistency · llm_judge (always gated)          │
              └──────────────────────┬───────────────────────────────┘
                                     │
              ┌──────────────────────▼───────────────────────────────┐
              │                  Task Ledger                          │
              │                                                      │
              │  Atomic writes (temp + os.replace) · FileLock         │
              │  ledger.json · progress.md · reset_in_progress()      │
              │                                                      │
              │  PENDING ──▶ IN_PROGRESS ──▶ VERIFYING ──▶ DONE      │
              │                  │ crash        │                     │
              │                  ▼ recovery     ▼                     │
              │               PENDING        FAILED ──▶ ABANDONED     │
              └──────────────────────┬───────────────────────────────┘
                                     │
         ┌───────────────┬───────────┴───────────┬───────────────────┐
         │               │                       │                   │
    ┌────▼────┐   ┌──────▼──────┐   ┌────────────▼───┐   ┌──────────▼──┐
    │Providers│   │  Storage    │   │ Observability  │   │  Entropy    │
    │         │   │             │   │                │   │             │
    │ LiteLLM │   │ LocalJSON   │   │ OTel Tracer    │   │ EntropyGC   │
    │ (circuit│   │ Redis       │   │ JSONL fallback │   │ 9 checks    │
    │ breaker)│   │ Postgres    │   │ Dashboard:7474 │   │ read-only   │
    │ Mock    │   └─────────────┘   └────────────────┘   └─────────────┘
    └─────────┘
         │
    ┌────▼────────────────────────────────────────────────────────────┐
    │                    SkillLibrary                                  │
    │  Bayesian reliability scoring · 4-gate admission control         │
    │  Cosine dedup · Post-run extraction · Verified procedure memory  │
    └─────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────┐
    │                    Security Layer                                │
    │  TrustedExecutor: 5-layer ACI injection defense                  │
    │  OutputSanitizer · Provenance tokens · Quarantine logging        │
    │  IdentityGuard: secret scrubbing on all output surfaces          │
    └─────────────────────────────────────────────────────────────────┘
```

---

## Key features

**Verification** — 10 built-in verifiers (bash exit code, schema validation, quote matching, HTTP status, file existence, semantic grounding, self-consistency, composite AND/OR chains, LLM judge). Write custom verifiers by extending `BaseVerifier`. Plugin autodiscovery via entry-points.

**Crash safety** — Atomic ledger with `os.replace()`. Kill the process at any point, re-run, and it resumes exactly where it left off. Zero duplicate work.

**Context management** — Frozen 6-step prompt assembly. Automatic compaction at 85% token budget. System prompt and last 3 exchanges are never compacted.

**Hooks** — Middleware system for cost tracking, rate limiting, human review gates, Slack notifications, and cross-run consistency detection. Hook errors are always caught — one broken hook never kills a run.

**SkillLibrary** — Extracts reusable procedures from completed tasks. Bayesian lower-bound reliability scoring. 4-gate admission control (confidence, retry count, step count, cosine dedup).

**Security** — `TrustedExecutor` applies 5-layer injection detection to every command output before it reaches agent context. `IdentityGuard` scrubs secrets from all output surfaces.

**Provider agnostic** — Built on LiteLLM with circuit breaker, exponential backoff, and fallback model chains.

---

## Getting started

```bash
git clone https://github.com/AV-CSE31/veridian
cd veridian
pip install -e ".[dev]"
pytest -q   # 274 tests
```

```python
from veridian import TaskLedger, Task, VeridianRunner, LiteLLMProvider

ledger = TaskLedger("ledger.json")
ledger.add([
    Task(
        title="Classify content",
        description="Classify this item. Output: decision (ALLOW/FLAG/REMOVE), reasoning.",
        verifier_id="schema",
        verifier_config={"required_fields": ["decision", "reasoning"]},
    )
])

runner = VeridianRunner(ledger=ledger, provider=LiteLLMProvider())
runner.add_hook("cost_guard", config={"max_cost_usd": 10.0})
summary = runner.run()
```

See [docs/customisation-guide.md](docs/customisation-guide.md) for writing custom verifiers, hooks, and storage backends.

---

## Built-in verifiers

| ID | Description | Use when |
|----|-------------|----------|
| `bash_exit` | Run command, pass if exit code 0 | Tests, compilation, scripts |
| `schema` | Validate structured output fields | Enforce output format |
| `quote_match` | Verify verbatim quote in source file | Legal extraction, citations |
| `http_status` | HTTP request, check status + body | API validation |
| `file_exists` | File presence, size, content checks | Artifact generation |
| `composite` | AND chain — all must pass | Multi-criterion tasks |
| `any_of` | OR chain — first pass wins | Flexible success criteria |
| `semantic_grounding` | Cross-field consistency, range checks | Hallucination detection |
| `self_consistency` | Generate N times, check agreement | High-stakes decisions |
| `llm_judge` | LLM evaluation (always inside composite) | Subjective quality |

---

## Module status

| Package | Status | Description |
|---------|--------|-------------|
| `core/` | ✅ | Task, events, exceptions, quality gate, config |
| `ledger/` | ✅ | Atomic ledger, crash recovery, progress log |
| `verify/` | ✅ | 10 verifiers + plugin registry |
| `hooks/` | ✅ | 6 built-in hooks |
| `agents/` | ✅ | Initializer, Worker, Reviewer agents |
| `context/` | ✅ | Frozen 6-step assembly, 85% compaction |
| `loop/` | ✅ | VeridianRunner, ParallelRunner |
| `providers/` | ✅ | LiteLLM + MockProvider |
| `skills/` | ✅ | Bayesian SkillLibrary |
| `storage/` | 🔲 | LocalJSON, Redis, Postgres — Phase 6 |
| `observability/` | 🔲 | OTel tracer, dashboard — Phase 6 |
| `entropy/` | 🔲 | EntropyGC (9 checks) — Phase 6 |
| `cli/` | 🔲 | Typer CLI — Phase 7 |

---

## Roadmap

### v1.0.0

- **Phase 6** — Observability (OTel GenAI v1.37+, JSONL fallback, FastAPI dashboard), storage backends (LocalJSON, Redis, Postgres), EntropyGC
- **Phase 7** — Full CLI (`init`, `run`, `status`, `gc`, `reset`, `retry`, `report`) via Typer + Rich
- **Phase 2+** — Verification policy templates for common domains
- **Phase 3+** — Secrets provider abstraction + IdentityGuard hook

### Post v1.0

| Feature | Description |
|---------|-------------|
| **MCP Skill Server** | Expose SkillLibrary via MCP — works with Claude Code, Cursor, Windsurf |
| **Proactive Scheduler** | Cron/interval/event-driven autonomous runs |
| **Tiered Memory** | Working/long-term/cold memory with aging policies |
| **Hierarchical Skills** | Nested skill composition from verified sub-skills |
| **Skill Provenance** | Full audit trail: extraction through reuse |
| **Cross-Agent Sharing** | Federated skill exchange via MCP protocol |
| **Policy Engine** | Declarative rules for execution, cost limits, approvals |
| **Multi-Agent Orchestration** | Agent-to-agent delegation, shared context pools |
| **Distributed Execution** | Horizontal scaling with distributed locking |
| **Evaluation Framework** | Automated benchmarking of verifiers, skills, agents |

Full strategic plan: [docs/ROADMAP_PHASE8_PLUS.md](docs/ROADMAP_PHASE8_PLUS.md)

---

## Comparison

| Feature | Veridian | LangGraph | AutoGen | OpenAI Agents SDK |
|---------|----------|-----------|---------|------------------|
| Crash-safe atomic ledger | ✅ | — | — | — |
| Deterministic verification | ✅ | — | — | — |
| Semantic grounding | ✅ | — | — | — |
| Cross-run consistency | ✅ | — | — | — |
| ACI injection defense | ✅ | — | — | — |
| Context compaction | ✅ | ⚠️ | — | ⚠️ |
| OTel GenAI conventions | ✅ | ⚠️ | — | — |
| Provider agnostic | ✅ | ✅ | ✅ | — |
| Plugin autodiscovery | ✅ | — | — | — |

---

## Contributing

Contributions welcome. Areas where help is most valuable:

- Domain-specific verifier packages (legal, compliance, data engineering)
- Storage backends (MongoDB, DynamoDB)
- Example pipelines for new domains
- MCP tool integrations

---

## License

MIT — see [LICENSE](LICENSE).
