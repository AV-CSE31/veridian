# Pydantic AI Integration

Use Pydantic AI for typed Python agent applications and durable execution
through workflow backends. Use Veridian when typed output is not enough and a
task must pass an external verifier before it counts as complete.

## Support Status

Support level: universal verification pattern.

There is no first-class Pydantic AI adapter in this repository yet. The
production path is to verify Pydantic AI result data at durable workflow
completion boundaries.

## Production Architecture

```text
Pydantic AI agent
  -> typed result
  -> Veridian verifier checks completion contract
  -> durable workflow commits only after verifier pass
  -> verifier failure triggers retry/review/failure state
```

Pydantic AI can make the result shape valid. Veridian verifies that the result
satisfies the task-specific completion contract.

## Minimal Verified Result

```python
from pydantic import BaseModel

from veridian.integrations.universal import UniversalVerifier


class Decision(BaseModel):
    decision: str
    reason: str


result = Decision(decision="approve", reason="all policy checks passed")

verifier = UniversalVerifier(
    verifiers=["schema"],
    schema_config={"required_fields": ["decision", "reason"]},
)
check = verifier.check(output=result.model_dump(), task_id="pydantic-ai-decision")

if not check.passed:
    raise RuntimeError(f"Durable workflow completion rejected: {check.error}")
```

## Production Hardening

- Run Veridian after Pydantic validation and before durable workflow commit.
- Persist the durable workflow run ID and backend name in Veridian metadata.
- Keep verifier configs versioned next to Pydantic models.
- Treat a schema-valid but verifier-invalid result as a real failed completion,
  not a serialization error.
- Add retry budgets so verifier failures cannot loop forever.

## Adapter Candidate Scope

A future adapter should map Pydantic AI durable execution IDs to Veridian
`TaskLedger` entries, persist replay snapshots at agent boundaries, and expose
one decorator for verified durable completion.

