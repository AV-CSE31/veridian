<p align="center">
  <img src="logo.png" alt="Veridian" width="420">
</p>

<h1 align="center">Veridian</h1>

<p align="center"><strong>Deterministic verification and reliability infrastructure for AI agent workflows.</strong></p>

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/veridian-ai.svg)](https://pypi.org/project/veridian-ai/)

Veridian helps teams run agent workflows with stronger runtime guarantees:

- deterministic verification of outputs
- crash-safe task state with atomic persistence
- replay/debug evidence for failures
- hook-based observability and policy controls

## What Veridian is (today)

Veridian is best used as a reliability and verification layer around agent tasks and workflows.

It provides:

- `TaskLedger` for durable task lifecycle state
- `VeridianRunner` / `ParallelRunner` for controlled execution
- built-in verifier and hook registries with plugin support
- CLI tooling for runs, status, retries, skip/reset, and entropy checks

## Install

```bash
pip install veridian-ai
```

Optional extras:

```bash
pip install "veridian-ai[llm]"        # LiteLLM provider
pip install "veridian-ai[otel]"       # OpenTelemetry exporter
pip install "veridian-ai[dashboard]"  # Dashboard features
pip install "veridian-ai[redis]"      # Redis storage backend
pip install "veridian-ai[postgres]"   # Postgres storage backend
pip install "veridian-ai[all]"        # All optional integrations
```

## Quick Start (Python)

### 1. Function-level guard

```python
from veridian.decorator import verified


@verified(verifiers=["not_none", "not_empty"])
def classify_text(text: str) -> dict:
    return {"decision": "ALLOW", "reason": "No harmful content detected."}


result = classify_text("review this content")
print(result)
```

### 2. Ledger-backed task execution

```python
from veridian import LiteLLMProvider, Task, TaskLedger, VeridianRunner

ledger = TaskLedger("ledger.json")
ledger.add(
    [
        Task(
            title="Run auth tests",
            description="Run tests and confirm pass.",
            verifier_id="bash_exit",
            verifier_config={"command": "pytest tests/test_auth.py -q"},
        )
    ]
)

runner = VeridianRunner(ledger=ledger, provider=LiteLLMProvider())
summary = runner.run()
print(summary.done_count, summary.failed_count)
```

## Quick Start (CLI)

```bash
veridian init --ledger ledger.json
veridian status --ledger ledger.json
veridian list --ledger ledger.json
veridian run --ledger ledger.json --dry-run
veridian run --ledger ledger.json --share-report
veridian gc --ledger ledger.json
```

Primary CLI commands currently available:

- `init`
- `status`
- `list`
- `run`
- `gc`
- `reset`
- `skip`
- `retry`
- `dlq status`
- `dlq list`
- `dlq retry`
- `dlq dismiss`
- `dlq report`
- `replay show`
- `replay compare`
- `replay diff`

## Release Readiness (2026-04-07)

Current release-gate status in this branch:

- `ruff check .` and `ruff format --check .` pass
- `mypy veridian/ --strict` passes
- full regression suite (`pytest`) passes with **2009 passed, 14 skipped**
- unit suite (`pytest tests/unit/ -x --tb=short -q`) passes
- integration release suite (pause/resume, replay, adapters, parity, subgraph) passes
- packaging gates pass: `uv build` + `twine check dist/*.whl dist/*.tar.gz`

World-class platform track status:

- Completed in code/tests: `WCP-001..007`, `WCP-009..010`, `WCP-012..019`, `WCP-021..029`
- Remaining documented follow-ups: `WCP-008`, `WCP-011`, `WCP-020`
- Newly integrated in this pass: operator plane, observability ingest/SLO/alerts, PII policy + trace filter, graph runtime, activity boundary expansion, plugin SDK/registry/certification

Known local environment caveats:

- The previous `litellm_init.pth` startup warning (`WinError 206`) was traced to a compromised user-site startup hook and has been remediated in this environment.
- Full coverage gate now passes locally (`pytest --cov=veridian --cov-fail-under=85 -q`).

Recommended local hardening for contributors:

- Keep user-site startup hooks (`*.pth`) minimal and auditable.
- If local Python startup behavior looks suspicious, run gates with `PYTHONNOUSERSITE=1`.

For contributor handoff docs and operational playbooks:

- [planning/README.md](planning/README.md)
- [planning/RELEASE_GATES.md](planning/RELEASE_GATES.md)
- [planning/runbooks/README.md](planning/runbooks/README.md)

## Built-in Components

Current built-in verifiers include:

- `bash_exit`, `schema`, `quote_match`, `http_status`, `file_exists`
- `composite`, `any_of`
- `semantic_grounding`, `self_consistency`, `llm_judge`
- `tool_safety`, `memory_integrity`
- `state_diff`, `prm_reference`

Current built-in hooks include:

- `LoggingHook`, `CostGuardHook`, `HumanReviewHook`, `RateLimitHook`
- `SlackNotifyHook`, `CrossRunConsistencyHook`, `AnomalyDetectorHook`
- `IdentityGuardHook`, `AdaptiveSafetyHook`, `EvolutionMonitorHook`
- `BehavioralFingerprintHook`, `DriftDetectorHook`, `BoundaryFluidityHook`

## Architecture and Maturity

Veridian is under active hardening. The core runtime path is:

`TaskLedger -> Runner -> Worker -> Verifier -> Ledger update`

Some advanced subsystems are implemented and evolving, but adoption should prioritize the core reliability path first. Keep production integrations claim-based and backed by tests.

## Development

Clone and install:

```bash
git clone https://github.com/AV-CSE31/veridian
cd veridian
pip install -e ".[dev]"
```

Run quality gates:

```bash
ruff check .
ruff format --check .
mypy veridian/ --strict
pytest -q --tb=short
pytest --cov=veridian --cov-fail-under=85 -q
```

Pre-commit hooks:

```bash
pre-commit install
pre-commit run --all-files
```

## Contributing

Contributions are welcome. For meaningful changes:

1. write or update tests first
2. keep changes scoped and module-boundary aware
3. run full quality gates before opening a PR
4. include rollback or failure-mode notes for risky runtime changes

Use GitHub issues for bugs/feature requests:

- [Issues](https://github.com/AV-CSE31/veridian/issues)
- [Discussions](https://github.com/AV-CSE31/veridian/discussions)

## License

MIT. See [LICENSE](LICENSE).
