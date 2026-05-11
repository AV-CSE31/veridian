# Veridian Integration Guides

Veridian is the verification and evidence layer for agent workflows. Use your
existing orchestration framework for graph control, agents, memory, tools, and
UI. Use Veridian when a workflow step must not count as complete until a
deterministic verifier accepts the output.

## Support Levels

| Framework | Support level | Production path |
|---|---|---|
| LangGraph | Certified adapter | `veridian.integrations.langgraph.VeridianLangGraph` |
| CrewAI | Certified adapter | `veridian.integrations.crewai.VeridianCrew` |
| OpenAI Agents SDK | Universal verification pattern | `UniversalVerifier` or `VerificationGate` around final outputs/tool results |
| Pydantic AI | Universal verification pattern | Verify typed agent results before durable workflow completion |
| Mastra | Universal sidecar pattern | Call Veridian from TypeScript via Python worker/CLI/API boundary |

Certified adapter means the repository has integration tests for adapter
behavior, verification failure handling, checkpoint persistence, replay report
generation, and compatibility warnings where applicable.

Universal verification pattern means Veridian can be used in production by
calling the stable integration SDK or universal verifier from the framework
boundary, but the repository does not yet ship a first-class framework adapter.

## Production Contract

Every production integration should make these decisions explicit:

1. Which framework step is being verified.
2. Which Veridian verifier owns the completion contract.
3. Whether verifier failure raises, retries, pauses, or routes to human review.
4. Where checkpoint and replay evidence are persisted.
5. Which framework version and Veridian version were tested together.
6. How operators inspect failed verifier evidence after an incident.

## Pages

- [LangGraph](langgraph.md)
- [CrewAI](crewai.md)
- [OpenAI Agents SDK](openai-agents-sdk.md)
- [Pydantic AI](pydantic-ai.md)
- [Mastra](mastra.md)
- [Production checklist](production-checklist.md)

## Production Track

The v0.4/v0.5 production track lives in [production guides](../production/).
Until the relevant adapter or sidecar tests exist, Pydantic AI, Mastra,
OpenAI Agents SDK, and Inspect AI must remain preview or universal patterns,
not certified integrations.
