<p align="center">
  <img src="logo.png" alt="Veridian" width="200">
</p>

<h1 align="center">Veridian</h1>

<p align="center"><strong>Deterministic verification infrastructure for autonomous AI agents.</strong></p>

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1312_passing-brightgreen.svg)]()
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

> **Every agent framework gives you a loop. Veridian gives you a guarantee.**

Long-running AI agents fail in production not because models are incapable, but because the infrastructure is missing. Veridian is the verification contract between your agent and the real world — ensuring tasks are deterministically verified, states are crash-safe, and behaviors remain aligned.

---

## The Problem vs. The Veridian Solution

| Vulnerability | What Happens | How Veridian Solves It |
| :--- | :--- | :--- |
| **Self-Certification** | Agent says "done" — system blindly believes it | `BaseVerifier` enforces deterministic Python checks. Never trust the LLM. |
| **Volatile State** | Process crash at step 47/100 = start over | `TaskLedger` uses POSIX-atomic writes. Resume exactly where you left off. |
| **Context Rot** | Context windows fill silently; agents hallucinate | `ContextCompactor` compresses at 85% capacity, preserves critical context. |
| **Contradictions** | Task 3: "LOW risk". Task 47: "CRITICAL risk" | `CrossRunConsistencyHook` checks logical claims across all tasks. |
| **Execution Vulnerability** | Injected instructions execute unchecked | `TrustedExecutor` applies 5-layer ACI injection defense + AST analysis. |
| **Behavioral Drift** | Pass rates erode from 95% to 80% over weeks | `DriftDetectorHook` uses Bayesian regression to detect behavioral shifts. |

---

## Quick Start

### The 3-Line Function Guard

Wrap any Python function with deterministic verification:

```python
from veridian import verified

@verified(verifier="schema", config={"required_fields": ["decision", "reasoning"]})
def classify_content(text: str) -> dict:
    return {"decision": "ALLOW", "reasoning": "Content meets safety guidelines."}

result = classify_content("Evaluate this input.")  # verified automatically
```

### The Crash-Safe Task Pipeline

For long-running, multi-step workflows:

```python
from veridian import TaskLedger, Task, VeridianRunner, LiteLLMProvider

ledger = TaskLedger("ledger.json")
ledger.add([
    Task(
        title="Migrate Authentication Module",
        description="Migrate src/auth.py to Python 3.11 syntax. Verify via pytest.",
        verifier_id="bash_exit",
        verifier_config={"command": "pytest tests/test_auth.py -v"},
    )
])

summary = VeridianRunner(ledger=ledger, provider=LiteLLMProvider()).run()
# Kill this process at any point. Re-run. It picks up exactly where it left off.
```

---

## Installation

```bash
pip install veridian-ai              # Core verification engine
pip install "veridian-ai[llm]"       # Core + LiteLLM provider
```

<details>
<summary><b>Optional enterprise plugins</b></summary>

```bash
pip install "veridian-ai[postgres]"  # PostgresStorage backend
pip install "veridian-ai[redis]"     # RedisStorage backend
pip install "veridian-ai[otel]"      # OpenTelemetry exporter
pip install "veridian-ai[dashboard]" # FastAPI SSE dashboard (port 7474)
pip install "veridian-ai[all]"       # Full enterprise suite
```

</details>

<details>
<summary><b>From source</b></summary>

```bash
git clone https://github.com/AV-CSE31/veridian
cd veridian
pip install -e ".[dev]"
pytest -q   # 1312 tests
```

</details>

---

## Architecture & Capabilities

<details>
<summary><b>12 Built-in Verifiers</b></summary>

Write custom verifiers by extending `BaseVerifier`. Plugin autodiscovery via entry-points.

| ID | Description | Use Case |
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

</details>

<details>
<summary><b>12 Built-in Hooks</b></summary>

Priority-ordered middleware pipeline. Hook errors are caught and isolated — a single monitoring failure never kills a production run.

| Hook | Priority | Description |
|------|----------|-------------|
| `LoggingHook` | 0 | Structured JSON logging on every lifecycle event |
| `IdentityGuardHook` | 5 | Proactive secret redaction from all output surfaces |
| `AdaptiveSafetyHook` | 45 | Trust-based verification scaling (4 levels) |
| `CostGuardHook` | 50 | Token-to-USD tracking, halts run at cost limit |
| `HumanReviewHook` | 50 | Pauses run when review criteria are met |
| `RateLimitHook` | 50 | Sliding window rate limiting with inter-task delay |
| `SlackNotifyHook` | 50 | Webhook notifications, silent on missing config |
| `CrossRunConsistencyHook` | 50 | Detects contradictions across tasks in a run |
| `AnomalyDetectorHook` | 55 | Mid-run token spikes, novel tool usage, output shifts |
| `EvolutionMonitorHook` | 85 | 6-pathway misevolution detection |
| `BehavioralFingerprintHook` | 88 | 7-dimensional cosine similarity divergence |
| `DriftDetectorHook` | 90 | Bayesian behavioral regression detection across runs |

</details>

<details>
<summary><b>Anti-Misevolution Safety (6 Pathways)</b></summary>

Based on *Misevolution* (NeurIPS 2025) and *Agents of Chaos* (Feb 2026):

| # | Pathway | Detection |
|---|---------|-----------|
| 1 | **Model** — safety refusal erosion | Refusal rate tracking vs baseline |
| 2 | **Memory** — biased experience accumulation | Contradiction rate, reward hacking |
| 3 | **Tool** — insecure code generation | AST analysis, blocked imports |
| 4 | **Workflow** — safety node pruning | Verification step completion rate |
| 5 | **Environment** — shared env corruption | Resource access anomaly index |
| 6 | **Evaluation** — eval code tampering | SHA-256 verifier fingerprinting |

</details>

<details>
<summary><b>Evolution Safety & Canary Tasks</b></summary>

**EvolutionGate** — three hard gates for agent self-modification:
1. Canary regression -> REJECT (non-negotiable)
2. Safety degradation -> REJECT
3. Both safe and capable -> APPROVE

**CanarySuite** — held-out tasks the agent never sees during self-improvement. Any regression blocks evolution.

**BehavioralFingerprint** — 7-dimensional signature per run. Cosine similarity below threshold triggers alert.

**CoT Alignment Auditing** — inspects reasoning traces for goal hijacking, sycophancy, alignment mirages, and specification contradictions.

</details>

<details>
<summary><b>MCP Skill Server + Federated Trust</b></summary>

- Expose verified procedures to Claude Code, Cursor, Windsurf via MCP protocol
- Skills filtered by Bayesian reliability lower bound
- Cross-organization skill sharing with independent trust scores
- Imported skills always go through quarantine
- Full provenance chain attached to every shared skill

</details>

<details>
<summary><b>Enterprise & Compliance</b></summary>

- **EU AI Act & NIST RMF:** Cryptographic proof chain with SHA-256 hash-linked entries and optional HMAC signing. Every task traceable to model version and policy attestation.
- **OWASP Agentic Top 10:** Built-in safeguards for prompt injection, insecure execution, and unverified tool outputs.
- **OpenTelemetry:** GenAI Semantic Conventions v1.37+ with JSONL fallback. SSE dashboard on port 7474.

</details>

---

## Framework Comparison

| Feature | Veridian | LangGraph | AutoGen | CrewAI |
|---------|:---:|:---:|:---:|:---:|
| Crash-safe atomic ledger | **Yes** | — | — | — |
| Deterministic verification (12 verifiers) | **Yes** | — | — | — |
| Anti-misevolution safety (6 pathways) | **Yes** | — | — | — |
| Behavioral drift detection | **Yes** | — | — | — |
| ACI injection defense (5-layer) | **Yes** | — | — | — |
| Cryptographic proof chain | **Yes** | — | — | — |
| MCP skill server | **Yes** | — | — | — |
| Chain-of-thought alignment audit | **Yes** | — | — | — |
| Adaptive trust-based safety | **Yes** | — | — | — |
| Context compaction | **Yes** | Partial | — | — |
| Bayesian skill memory | **Yes** | — | — | — |
| Provider agnostic | **Yes** | Yes | Yes | Yes |

---

## Module Status

| Package | Status | Description |
|---------|:---:|-------------|
| `core/` | Shipped | Task, events, exceptions, config, quality gates |
| `ledger/` | Shipped | Atomic ledger, crash recovery, progress log |
| `verify/` | Shipped | 12 verifiers + integrity checker + plugin registry |
| `hooks/` | Shipped | 12 hooks (logging through drift detection) |
| `agents/` | Shipped | Initializer, Worker, Reviewer agents |
| `context/` | Shipped | Frozen 6-step assembly, 85% compaction |
| `loop/` | Shipped | VeridianRunner, ParallelRunner |
| `providers/` | Shipped | LiteLLM (circuit breaker) + MockProvider |
| `skills/` | Shipped | Bayesian SkillLibrary + quarantine + blast radius |
| `storage/` | Shipped | LocalJSON, Redis, PostgreSQL |
| `observability/` | Shipped | OTel tracer, dashboard, proof chain, compliance reports |
| `contracts/` | Shipped | Sprint contracts + HMAC signing |
| `eval/` | Shipped | Adversarial evaluator, canary suite, evolution sandbox |
| `secrets/` | Shipped | SecretsProvider ABC + EnvSecretsProvider |
| `mcp/` | Shipped | MCP Skill Server + Federated Trust |
| `protocols/` | Shipped | Safety-aware evolution gate |
| `cli/` | Shipped | Typer + Rich (init, run, status, gc, reset, retry, skip, report) |

---

## Who Is This For?

- **Teams deploying AI agents to production** who need guarantees beyond "the model said it's done"
- **Compliance, legal, and financial pipelines** where hallucinated output has real consequences
- **Long-running multi-task workflows** that need crash recovery and consistency checking
- **Agent reliability researchers** working on autonomous reasoning systems
- **Teams building on LangChain/LangGraph/AutoGen** who need a verification layer those frameworks don't provide

---

## Contributing

Veridian is in **public beta** under active development. We welcome contributions of all kinds.

### Areas Where Help Is Most Valuable

| Area | Examples |
|------|---------|
| **Domain verifiers** | Healthcare, legal, data engineering, compliance |
| **Storage backends** | MongoDB, DynamoDB, S3 |
| **Example pipelines** | Real-world use cases for new domains |
| **MCP integrations** | Connecting verified procedures to dev tools |
| **Documentation** | Tutorials, guides, API reference |
| **Research** | Misevolution benchmarks, safety experiments |

### How to Contribute

1. Fork the repo and create a feature branch
2. Write tests first (`tests/unit/test_<module>.py`)
3. Implement your changes
4. Ensure all quality gates pass:
   ```bash
   ruff check . && ruff format --check .
   mypy veridian/ --strict
   pytest -x --tb=short -q
   ```
5. Open a PR with a clear description

### Get in Touch

| Channel | Link |
|---------|------|
| Issues | [github.com/AV-CSE31/veridian/issues](https://github.com/AV-CSE31/veridian/issues) |
| Discussions | [github.com/AV-CSE31/veridian/discussions](https://github.com/AV-CSE31/veridian/discussions) |
| PyPI | [pypi.org/project/veridian-ai](https://pypi.org/project/veridian-ai/) |

---

## Support the Project

If Veridian is useful to your work:

- **Star the repo** — helps others discover the project
- **Share your use case** — open a discussion to tell us how you're using it
- **Report bugs** — detailed issue reports help us improve fastest
- **Write about it** — blog posts, tweets, and conference talks all help
- **Contribute code** — even a single test or documentation fix matters

---

## License

MIT — see [LICENSE](LICENSE).

---

<sub>1312 tests | 12 verifiers | 12 hooks | 3 storage backends | 6 misevolution pathways | EU AI Act ready | Python 3.11+ | MIT</sub>
