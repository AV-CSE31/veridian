# LangGraph Integration

Use LangGraph for durable graph orchestration. Use Veridian to decide whether a
node output is allowed to count as verified completion.

## Support Status

Support level: certified adapter.

Adapter: `veridian.integrations.langgraph.VeridianLangGraph`

Test evidence:

- `tests/integration/test_langgraph_adapter.py`
- `tests/integration/test_langgraph_certification.py`
- `tests/integration/test_certification_matrix.py`

Supported version families in code: `0.2`, `0.3`, `0.4`.

The adapter can run in duck-typed mode for hermetic tests or compatible
LangGraph graph objects that expose `stream(state)` or `invoke(state)`. When an
installed LangGraph version is outside the supported families, the adapter emits
`LangGraphCompatibilityWarning`.

## Production Architecture

```text
LangGraph graph
  -> node emits output
  -> VeridianLangGraph records a TraceStep
  -> optional VerificationContract runs a verifier for that node
  -> checkpoint evidence is persisted to TaskLedger
  -> verifier failure raises VerificationError when on_failure="raise"
```

LangGraph controls graph flow. Veridian controls whether a node output satisfies
the completion contract.

## Minimal Verified Edge

```python
from pathlib import Path

from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.integrations.langgraph import VeridianLangGraph, VerificationContract
from veridian.integrations.sdk import start_run
from veridian.ledger.ledger import TaskLedger
from veridian.providers.litellm_provider import LiteLLMProvider

config = VeridianConfig(
    ledger_file=Path("ledger.json"),
    progress_file=Path("progress.md"),
    strict_replay=True,
    activity_journal_enabled=True,
)
ledger = TaskLedger(config.ledger_file, progress_file=str(config.progress_file))
provider = LiteLLMProvider()

task = Task(
    title="Draft customer response",
    verifier_id="schema",
    verifier_config={"required_fields": ["summary", "decision"]},
)
ledger.add([task])

ctx = start_run(config=config, provider=provider, ledger=ledger)
contract = VerificationContract(
    verifiers={"draft": "schema"},
    verifier_configs={"draft": {"required_fields": ["summary", "decision"]}},
    on_failure="raise",
)

verified_graph = VeridianLangGraph(
    graph=compiled_graph,
    sdk_context=ctx,
    task=task,
    contract=contract,
)

final_state = verified_graph.invoke({"customer_id": "cust_123"})
```

## Production Hardening

- Pin LangGraph and Veridian versions in application lockfiles.
- Keep `strict_replay=True` for incident-sensitive workflows.
- Use one `VerificationContract` per graph boundary where false completion has
  material risk.
- Store `ledger.json` on durable storage or use a runtime storage bridge for
  Redis/Postgres-backed deployments.
- Treat `VerificationError` as a workflow-control signal, not a generic
  exception. Route it to retry, pause, DLQ, or human review.
- Run adapter certification tests in CI when changing framework versions.

## Failure Modes

| Failure | Behavior | Operator action |
|---|---|---|
| Node output misses verifier contract | `VerificationError` | inspect verifier error and repair/retry |
| Framework graph raises | `LangGraphAdapterError` | inspect framework stack and graph state |
| Unsupported version | warning | run certification tests before production rollout |
| Replay snapshot drift | replay report shows incompatibility | rerun only after accepting model/prompt/config drift |

