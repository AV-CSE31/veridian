# Veridian

**Deterministic verification infrastructure for autonomous AI agents.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/AV-CSE31/veridian/actions/workflows/test.yml/badge.svg)](https://github.com/AV-CSE31/veridian/actions)
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

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

## The Problem

Long-running AI agents fail not because models are incapable, but because infrastructure is missing:

| Failure mode | What happens | Veridian solution |
|---|---|---|
| Agents self-certify completion | Agent says "done" — system believes it | `BaseVerifier` — deterministic Python checks, never LLM |
| State lost on crash | Process kill at step 47/100 = start over | `TaskLedger` — atomic writes via `os.replace()`, auto-recovery |
| Context windows fill silently | Agents hallucinate as context degrades | `ContextCompactor` — 85% threshold, preserves critical context |
| Contradictions go undetected | Task 3: risk LOW. Task 47: risk CRITICAL | `CrossRunConsistencyHook` — checks claims across all tasks |
| Tool output trusted blindly | Injected instructions execute unchecked | `TrustedExecutor` — 5-layer ACI injection defense |
| Agent behavior drifts silently | Pass rate drops from 95% to 80% over weeks | `DriftDetectorHook` — Bayesian regression detection across runs |

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
                     │                          │ DriftDetector      │
                     │                          └───────────────────┘
              ┌──────▼───────────────────────────────────────────────┐
              │              Verification Layer                       │
              │                                                      │
              │  BaseVerifier ABC + VerifierRegistry (entry-points)   │
              │                                                      │
              │  bash_exit · schema · quote_match · http_status       │
              │  file_exists · composite · any_of · semantic_grounding│
              │  self_consistency · llm_judge (always gated)          │
              │  tool_safety · memory_integrity (Phase 6b)            │
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
    ┌─────────────────────────────────────────────────────────────────┐
    │               Verifier Integrity Checker                        │
    │  SHA-256 fingerprint at run start · tamper detection at run end  │
    └─────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────┐
    │                    Security Layer                                │
    │  TrustedExecutor: 5-layer ACI injection defense                  │
    │  OutputSanitizer · Provenance tokens · Quarantine logging        │
    │  IdentityGuard: secret scrubbing on all output surfaces          │
    └─────────────────────────────────────────────────────────────────┘
```

---

## Key Features

**Verification** — 12 built-in verifiers (bash exit code, schema validation, quote matching, HTTP status, file existence, semantic grounding, self-consistency, composite AND/OR chains, LLM judge, tool safety, memory integrity). Write custom verifiers by extending `BaseVerifier`. Plugin autodiscovery via entry-points.

**Crash safety** — Atomic ledger with `os.replace()`. Kill the process at any point, re-run, and it resumes exactly where it left off. Zero duplicate work.

**Drift detection** — `DriftDetectorHook` compares verification pass rates, confidence distributions, retry rates, and token usage across runs. Uses Bayesian Beta lower-bound analysis to detect statistically significant behavioral regression before production breaks.

**Context management** — Frozen 6-step prompt assembly. Automatic compaction at 85% token budget. System prompt and last 3 exchanges are never compacted.

**`@verified` decorator** — Wrap any Python function with deterministic verification. No restructuring into `Task` + `VeridianRunner` needed.

**Sprint Contracts** — Pre-execution commitment protocol between agent and evaluator. HMAC-SHA256 signed. Raises `ContractViolation` on breach.

**Adversarial Evaluator** — GAN-inspired structural separation of generator and judge. Independent LLM evaluates output against signed contracts with calibrated rubrics.

**Hooks** — Middleware system for cost tracking, rate limiting, human review gates, Slack notifications, cross-run consistency detection, and drift monitoring. Hook errors are always caught — one broken hook never kills a run.

**SkillLibrary** — Extracts reusable procedures from completed tasks. Bayesian lower-bound reliability scoring. 4-gate admission control (confidence, retry count, step count, cosine dedup).

**Anti-misevolution safety** — `ToolSafetyVerifier` uses AST-based static analysis to block eval/exec, shell injection, and blocked imports in agent-generated code. `MemoryIntegrityVerifier` detects reward hacking, prompt injection, and numeric drift in memory updates. `VerifierIntegrityChecker` SHA-256 fingerprints the verification chain to detect mid-run tampering.

**Security** — `TrustedExecutor` applies 5-layer injection detection to every command output before it reaches agent context. `IdentityGuard` scrubs secrets from all output surfaces.

**Provider agnostic** — Built on LiteLLM with circuit breaker, exponential backoff, and fallback model chains.

---

## Install

```bash
pip install veridian-ai

# With LLM provider support
pip install veridian-ai[llm]
```

### Optional extras

```bash
pip install veridian-ai[dashboard]   # FastAPI SSE dashboard (port 7474)
pip install veridian-ai[otel]        # OpenTelemetry exporter
pip install veridian-ai[redis]       # RedisStorage backend
pip install veridian-ai[postgres]    # PostgresStorage backend
pip install veridian-ai[all]         # Everything
```

### From source

```bash
git clone https://github.com/AV-CSE31/veridian
cd veridian
pip install -e ".[dev]"
pytest -q   # 741 tests
```

---

## Quick Start

### Hello World — verify a function in 3 lines

```python
from veridian import verified

@verified(verifier="schema", config={"required_fields": ["answer"]})
def classify(text: str) -> dict:
    return {"answer": "ALLOW", "reasoning": "Content is safe."}

result = classify("hello world")  # passes verification automatically
```

### Full pipeline — tasks with crash recovery

```python
from veridian import TaskLedger, Task, VeridianRunner, MockProvider

ledger = TaskLedger("ledger.json")
ledger.add([
    Task(
        title="Classify content",
        description="Classify this item. Output: decision (ALLOW/FLAG/REMOVE), reasoning.",
        verifier_id="schema",
        verifier_config={"required_fields": ["decision", "reasoning"]},
    )
])

# Kill this at any point. Re-run. Resumes exactly where it left off.
runner = VeridianRunner(ledger=ledger, provider=MockProvider())
summary = runner.run()
print(f"Done: {summary.done_count}, Failed: {summary.failed_count}")
```

---

## Built-in Verifiers

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
| `tool_safety` | AST-based static analysis on generated code | Agent code generation |
| `memory_integrity` | Validates memory updates for bias/tampering | Skill/memory updates |

---

## Built-in Hooks

| Hook | Priority | Description |
|------|----------|-------------|
| `LoggingHook` | 0 | Structured JSON logging on every lifecycle event |
| `CostGuardHook` | 50 | Token-to-USD tracking, halts run at cost limit |
| `HumanReviewHook` | 50 | Pauses run when review criteria are met |
| `RateLimitHook` | 50 | Sliding window rate limiting with inter-task delay |
| `SlackNotifyHook` | 50 | Webhook notifications, silent on missing config |
| `CrossRunConsistencyHook` | 50 | Detects contradictions across tasks in a run |
| `DriftDetectorHook` | 90 | Bayesian behavioral regression detection across runs |

---

## Module Status

| Package | Status | Description |
|---------|--------|-------------|
| `core/` | ✅ | Task, events, exceptions, quality gate, config |
| `ledger/` | ✅ | Atomic ledger, crash recovery, progress log |
| `verify/` | ✅ | 12 verifiers + integrity checker + plugin registry |
| `hooks/` | ✅ | 7 built-in hooks (including drift detection) |
| `agents/` | ✅ | Initializer, Worker, Reviewer agents |
| `context/` | ✅ | Frozen 6-step assembly, 85% compaction |
| `loop/` | ✅ | VeridianRunner, ParallelRunner |
| `providers/` | ✅ | LiteLLM + MockProvider |
| `skills/` | ✅ | Bayesian SkillLibrary |
| `storage/` | ✅ | LocalJSON, Redis, Postgres — `BaseStorage` ABC + 3 backends |
| `observability/` | ✅ | OTel GenAI v1.37+ tracer, JSONL fallback, FastAPI dashboard :7474 |
| `contracts/` | ✅ | Sprint contracts + HMAC signing |
| `eval/` | ✅ | Adversarial evaluator + calibration pipeline |
| `testing/` | ✅ | Recorder/replayer for deterministic test replay |
| `entropy/` | ✅ | EntropyGC — 9 read-only consistency checks, atomic report |
| `cli/` | ✅ | Typer + Rich CLI (init, run, status, gc, reset, retry, skip, report) |

---

## Who Is This For?

Veridian is for teams and individuals who are:

- **Deploying AI agents to production** and need guarantees beyond "the model said it's done"
- **Building compliance, legal, or financial pipelines** where hallucinated output has real consequences
- **Running long multi-task agent workflows** that need crash recovery and consistency checking
- **Researching agent reliability** — ARC-AGI, liquid intelligence, autonomous reasoning systems
- **Building on top of LangChain/LangGraph/AutoGen** but need a verification layer those frameworks don't provide

If you're building agents that make decisions people depend on, Veridian is the verification contract between your agent and the world.

---

## Roadmap

### Where we are

| Milestone | Status |
|-----------|--------|
| Core verification engine | ✅ Shipped |
| 12 built-in verifiers + plugin system | ✅ Shipped |
| Crash-safe atomic ledger | ✅ Shipped |
| Hook system + drift detection | ✅ Shipped |
| Bayesian SkillLibrary | ✅ Shipped |
| Sprint Contracts + Adversarial Eval | ✅ Shipped |
| `@verified` decorator | ✅ Shipped |
| Observability + Storage backends | ✅ Shipped |
| Anti-misevolution safety (Tool Safety, Memory Integrity, Verifier Integrity) | ✅ Shipped |
| CLI with Typer + Rich | ✅ Shipped |
| v0.2.0 Tier A quick-wins (config validation, path traversal guard) | ✅ Shipped |

### Where we're heading

**v0.2.0 — Foundation Safety**
- Evolution safety monitor + behavioral fingerprinting
- Sandbox isolation + canary tasks
- Secrets management + identity guard

**v0.3.0 — Inter-Agent Safety**
- Agent-to-agent handoff verification
- Skill quarantine + contamination blast radius
- Adaptive verification thresholds + anomaly detection

**v1.0.0 — Production Release (EU AI Act ready)**
- Cryptographic audit chain + compliance reports
- MCP Skill Server (Claude Code, Cursor, Windsurf integration)
- Federated trust across organizations

**Beyond v1.0**
- Safety-aware self-evolution
- Chain-of-thought auditing
- Research frontier: Impossible Trilemma experiments

---

## Comparison

| Feature | Veridian | LangGraph | AutoGen | CrewAI |
|---------|----------|-----------|---------|--------|
| Crash-safe atomic ledger | ✅ | — | — | — |
| Deterministic verification (12 verifiers) | ✅ | — | — | — |
| Anti-misevolution safety gates | ✅ | — | — | — |
| Verifier integrity (anti-eval-hacking) | ✅ | — | — | — |
| Semantic grounding | ✅ | — | — | — |
| Cross-run consistency | ✅ | — | — | — |
| Agent drift detection | ✅ | — | — | — |
| ACI injection defense | ✅ | — | — | — |
| Context compaction | ✅ | ⚠️ | — | — |
| Bayesian skill memory | ✅ | — | — | — |
| Sprint contracts + adversarial eval | ✅ | — | — | — |
| Provider agnostic | ✅ | ✅ | ✅ | ✅ |
| Plugin autodiscovery | ✅ | — | — | — |

---

## Contributing

Veridian is in **public beta** and under active development. Contributions are welcome.

### Areas where help is most valuable

- **Domain-specific verifier packages** — legal, compliance, healthcare, data engineering
- **Storage backends** — MongoDB, DynamoDB, S3
- **Example pipelines** — real-world use cases for new domains
- **MCP tool integrations** — connecting verified procedures to development tools
- **Documentation** — tutorials, guides, API reference

### How to contribute

1. Fork the repo and create a feature branch
2. Write tests first (`tests/unit/test_<module>.py`)
3. Implement your changes
4. Ensure all quality gates pass: `ruff check .`, `mypy veridian/ --strict`, `pytest`
5. Open a PR with a clear description

### Get in touch

- **Issues**: [github.com/AV-CSE31/veridian/issues](https://github.com/AV-CSE31/veridian/issues)
- **Discussions**: [github.com/AV-CSE31/veridian/discussions](https://github.com/AV-CSE31/veridian/discussions)

If you're working on agent reliability in production — whether in research, enterprise, or open source — we'd love to collaborate.

---

## License

MIT — see [LICENSE](LICENSE).
