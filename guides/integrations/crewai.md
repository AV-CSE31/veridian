# CrewAI Integration

Use CrewAI for role-based multi-agent execution. Use Veridian to verify CrewAI
task outputs before they become trusted production evidence.

## Support Status

Support level: certified adapter.

Adapter: `veridian.integrations.crewai.VeridianCrew`

Test evidence:

- `tests/integration/test_crewai_adapter.py`
- `tests/integration/test_crewai_certification.py`
- `tests/integration/test_certification_matrix.py`

Supported version families in code: `0.80` through `0.86`.

The adapter wraps CrewAI `kickoff()` and, when available, the CrewAI
`task_callback` boundary. It records per-task trace steps, preserves agent and
manager role metadata where available, verifies configured task outputs, and
persists checkpoint evidence to the ledger.

## Production Architecture

```text
CrewAI crew.kickoff()
  -> task_callback emits task output
  -> VeridianCrew records framework trace metadata
  -> CrewVerificationContract verifies selected task outputs
  -> checkpoint evidence is persisted to TaskLedger
  -> invalid output raises VerificationError when on_failure="raise"
```

CrewAI organizes the crew. Veridian verifies whether the work is acceptable.

## Minimal Verified Crew

```python
from pathlib import Path

from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.integrations.crewai import VeridianCrew, CrewVerificationContract
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
    title="Review wire transfer",
    verifier_id="schema",
    verifier_config={"required_fields": ["decision", "reason"]},
)
ledger.add([task])

ctx = start_run(config=config, provider=provider, ledger=ledger)
contract = CrewVerificationContract(
    verifiers={"analyst-review": "schema"},
    verifier_configs={"analyst-review": {"required_fields": ["decision", "reason"]}},
    on_failure="raise",
)

verified_crew = VeridianCrew(
    crew=crew,
    sdk_context=ctx,
    task=task,
    contract=contract,
)

result = verified_crew.kickoff({"payment_id": "pay_123"})
```

## Production Hardening

- Pin CrewAI and Veridian versions.
- Use stable task descriptions or names for contract keys. The adapter maps
  task outputs by description/name when task callback metadata is available.
- Configure verifier failure handling explicitly: raise, retry, DLQ, or human
  review.
- Keep ledger storage durable and included in backup/retention plans.
- Preserve `step_records` and replay reports for incident review.
- Run certification tests before upgrading CrewAI.

## Failure Modes

| Failure | Behavior | Operator action |
|---|---|---|
| Task output fails verifier | `VerificationError` | repair output, retry, or route to review |
| Crew kickoff runtime failure | `CrewKickoffError` | inspect CrewAI execution failure |
| Adapter compatibility issue | `CrewAdapterError` | inspect framework API mismatch |
| Unsupported old CrewAI version | `CrewVersionWarning` | run certification before rollout |

