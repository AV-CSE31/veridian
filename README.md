<p align="center">
  <img src="logo.png" alt="Veridian" width="420">
</p>

<h1 align="center">Veridian</h1>

<p align="center"><strong>Deterministic verification and replay-safe runtime for AI agent workflows.</strong></p>

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

Veridian is a reliability layer for agent execution.
It makes task completion deterministic: a task is not marked done unless its verifier passes.

## Why Veridian

Most agent stacks are strong at orchestration but weak at runtime guarantees.
Veridian focuses on guarantees:

- deterministic task verification (Python verifiers, not self-certification)
- crash-safe task state and replay evidence
- pause/resume and dead-letter queue operations
- policy/hook controls with isolated hook failures

## What Veridian Is (and Is Not)

What it is:

- a runtime correctness layer around agent tasks
- a verification contract system for task transitions
- a replay/debug substrate for production incidents

What it is not:

- not a replacement for your preferred orchestration framework
- not a prompt-only guardrail tool

Use Veridian with existing frameworks when you want stronger execution correctness.

## Install

```bash
pip install veridian-ai
```

Optional extras:

```bash
pip install "veridian-ai[llm]"        # LiteLLM provider support
pip install "veridian-ai[otel]"       # OpenTelemetry exporter support
pip install "veridian-ai[dashboard]"  # Dashboard endpoints
pip install "veridian-ai[redis]"      # Redis storage backend
pip install "veridian-ai[postgres]"   # Postgres storage backend
pip install "veridian-ai[all]"        # All optional integrations
```

## Quick Start (Python)

```python
from veridian.core.task import Task
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider

ledger = TaskLedger("ledger.json")
ledger.add(
    [
        Task(
            title="Sanity check output schema",
            description="Return JSON with keys: decision, reason.",
            verifier_id="schema",
            verifier_config={"required_fields": ["decision", "reason"]},
        )
    ]
)

provider = MockProvider().script_veridian_result(
    structured={"decision": "allow", "reason": "policy-pass"}
)
runner = VeridianRunner(ledger=ledger, provider=provider)
summary = runner.run()
print(summary.done_count, summary.failed_count)
```

## Quick Start (CLI)

```bash
veridian init --ledger ledger.json
veridian status --ledger ledger.json
veridian list --ledger ledger.json
veridian run --ledger ledger.json
veridian replay show --ledger ledger.json
veridian dlq status --ledger ledger.json
```

Core CLI commands:

- `init`, `status`, `list`, `run`, `gc`, `reset`, `skip`, `retry`
- `dlq status`, `dlq list`, `dlq retry`, `dlq dismiss`, `dlq report`
- `replay show`, `replay compare`, `replay diff`

## Core Runtime Model

Execution path:

`TaskLedger -> Runner -> Worker -> Verifier -> Ledger transition`

Typical lifecycle:

`PENDING -> IN_PROGRESS -> VERIFYING -> DONE`

Failure and intervention paths include:

- `FAILED` / `ABANDONED`
- `PAUSED` with resume support
- replay compatibility checks for drift-sensitive runs

## Built-in Components

Verifier families include:

- structural and IO checks (`schema`, `file_exists`, `http_status`, `bash_exit`)
- composition (`composite`, `any_of`)
- quality/safety checks (`semantic_grounding`, `self_consistency`, `llm_judge`, `tool_safety`, `memory_integrity`)

Hook families include:

- logging/cost/rate controls
- human review and consistency checks
- anomaly and drift-oriented safety hooks

## Quality Gates

Veridian uses claim-to-test discipline. For release work, run:

```bash
ruff check .
ruff format --check .
mypy veridian/ --strict
pytest -q --tb=short
pytest --cov=veridian --cov-fail-under=85 -q
```

## Roadmap Focus

Near-term focus areas:

- backend migration and operations docs
- deterministic step-level checkpoint cursor
- public compatibility matrix and migration guides

## Contributing

Contributions are welcome.

Recommended PR discipline:

1. add or update tests first
2. keep PRs single-ticket and scoped
3. update docs when behavior changes
4. include failure-mode and rollback notes for risky runtime changes

Project links:

- [Issues](https://github.com/AV-CSE31/veridian/issues)
- [Discussions](https://github.com/AV-CSE31/veridian/discussions)

## License

MIT. See [LICENSE](LICENSE).
