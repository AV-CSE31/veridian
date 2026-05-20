# Veridian

**Deterministic verification for AI agent work.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

Veridian is a small runtime that sits between an agent and the outside world.
Its core rule is simple:

> A task is not marked `DONE` unless an independent verifier passes.

The model can produce a result. Veridian decides whether that result is complete.

## Why

Agent frameworks are good at planning, routing, and tool use. Veridian focuses on
the narrower production boundary: verified completion.

Use it when you need:

- deterministic task state transitions
- crash-safe ledger writes
- retryable failures instead of silent success
- structured evidence for each completed task
- verifier logic that lives outside the model

Veridian is not an orchestration framework, dashboard, policy engine, or prompt
library. The `0.3.0` release is intentionally light: core runtime, ledger,
providers, hooks, and practical verifiers.

## Install

```bash
pip install veridian-ai
```

The base install is small and only requires `filelock`.

Optional extras:

```bash
pip install "veridian-ai[llm]"       # LiteLLM provider support
pip install "veridian-ai[http]"      # HTTP verifier support
pip install "veridian-ai[pdf]"       # PDF quote matching support
pip install "veridian-ai[pydantic]"  # Pydantic model validation
pip install "veridian-ai[all]"       # All runtime extras
```

## Quick Start

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from veridian import MockProvider, Task, TaskLedger, VeridianRunner

contract = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}

with TemporaryDirectory() as tmp:
    ledger = TaskLedger(
        Path(tmp) / "ledger.json",
        progress_file=str(Path(tmp) / "progress.md"),
    )
    ledger.add(
        [
            Task(
                title="Release gate",
                description="Decide whether build 2026.05 can ship.",
                verifier_id="schema",
                verifier_config={"schema": contract},
            )
        ]
    )

    provider = MockProvider().script_veridian_result(
        structured={
            "decision": "ship",
            "risk": "low",
            "reason": "tests, lint, and verification passed",
        }
    )

    summary = VeridianRunner(ledger=ledger, provider=provider).run()
    print(summary.to_dict())
```

Output:

```python
{"done_count": 1, "failed_count": 0, "total_tasks": 1, ...}
```

If the provider omits a required field, uses an invalid enum, or returns output
that cannot satisfy the verifier, the task becomes `FAILED`, not `DONE`.

## Decorator Example

For application code that already has a Python function boundary, use
`@verified` to attach the same verifier contract without creating a runner:

```python
from veridian import verified

contract = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}


@verified(verifier_id="schema", verifier_config={"schema": contract})
def decide_release() -> dict[str, str]:
    return {
        "decision": "ship",
        "risk": "low",
        "reason": "tests, lint, and verification passed",
    }


result = decide_release()
assert result.passed
```

See [`examples/decorator_release_gate.py`](examples/decorator_release_gate.py)
for a complete success-and-failure demonstration.

## Runtime Model

Veridian keeps the execution path deliberately small:

```text
Task -> TaskLedger -> VeridianRunner -> WorkerAgent -> Verifier -> TaskLedger
```

The ledger is the only component allowed to change task status. It writes
atomically and resets stale `IN_PROGRESS` tasks before each run, so interrupted
work can be retried safely.

Typical lifecycle:

```text
PENDING -> IN_PROGRESS -> VERIFYING -> DONE
PENDING -> IN_PROGRESS -> VERIFYING -> FAILED
```

## Public API

Top-level imports are intentionally narrow:

```python
from veridian import (
    BaseHook,
    BaseVerifier,
    HookRegistry,
    LiteLLMProvider,
    LLMProvider,
    LLMResponse,
    Message,
    MockProvider,
    ProviderError,
    RunSummary,
    Task,
    TaskLedger,
    VeridianConfig,
    VeridianError,
    VeridianRunner,
    VerifiedCall,
    VerificationError,
    VerificationResult,
    verified,
)
```

Everything else is available through explicit module paths.

## Built-In Verifiers

The `0.3.0` core includes practical verifier building blocks:

- `schema`: required fields, small JSON Schema subset, optional Pydantic models
- `file_exists`: require an artifact path to exist
- `bash_exit`: validate captured shell exit codes
- `http_status`: check an endpoint status code with the `http` extra
- `quote_match`: match quoted evidence, with PDF support behind the `pdf` extra
- `composite`: require multiple verifier checks
- `any_of`: pass when at least one verifier passes

## How It Fits

Veridian complements orchestration libraries:

- Use LangGraph, CrewAI, or your own loop to decide what work should happen.
- Use Pydantic when you want rich model validation.
- Use Veridian when you need the completion boundary to be enforced by a
  runtime ledger and independent verifier.

The design bias is closer to Pydantic's small public namespace and LangGraph's
focused runtime core than to an all-in-one agent platform.

## Development

```bash
python -m pytest -q
python -m ruff check veridian tests examples
```

Protected local planning and research material is intentionally not part of the
public package.

## License

MIT. See [LICENSE](LICENSE).
