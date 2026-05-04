# OpenAI Agents SDK Integration

Use the OpenAI Agents SDK for first-party agent execution, tracing, sessions,
tools, and guardrails. Use Veridian at completion boundaries where a deterministic
verifier must decide whether the output can be accepted.

## Support Status

Support level: universal verification pattern.

There is no first-class OpenAI Agents SDK adapter in this repository yet. The
production path is to call `UniversalVerifier`, `VerificationGate`, or the stable
integration SDK after important agent outputs and tool results.

## Production Architecture

```text
OpenAI agent run
  -> final output or tool result
  -> Veridian UniversalVerifier or integration SDK verify_output()
  -> pass: continue application workflow
  -> fail: raise, retry, pause, or route to human review
  -> optional: persist TaskLedger evidence for replay/audit
```

OpenAI guardrails can block unsafe or malformed behavior. Veridian adds an
independent completion contract that is stored as application evidence.

## Minimal Universal Gate

```python
from veridian.integrations.universal import UniversalVerifier

verifier = UniversalVerifier(
    verifiers=["schema"],
    schema_config={"required_fields": ["decision", "reason"]},
)

agent_output = {
    "decision": "allow",
    "reason": "all required controls passed",
}

check = verifier.check(output=agent_output, task_id="openai-agent-final")
if not check.passed:
    raise RuntimeError(f"OpenAI agent output rejected by Veridian: {check.error}")
```

## Production Hardening

- Keep OpenAI guardrails and Veridian verifiers separate in logs. They answer
  different questions.
- Persist the OpenAI trace/session/run IDs in Veridian task metadata when using
  a full `TaskLedger` flow.
- Use deterministic verifiers for acceptance criteria. Use LLM-based judging
  only as a last-resort secondary signal.
- Decide whether failed verification triggers retry, human review, or DLQ.
- Record the model, prompt/config version, verifier ID, and verifier config in
  deployment evidence.

## When To Build A First-Class Adapter

Build an adapter when the application needs automatic trace-step capture,
checkpoint persistence after each agent handoff, replay reports tied to OpenAI
session IDs, or uniform failure mapping into the Veridian error hierarchy.

