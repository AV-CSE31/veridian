# Production Guides

These pages define the public v0.4 and v0.5 production-readiness track.
They are intentionally narrower than a general agent-framework roadmap:
Veridian's job is verified completion, durable evidence, and replay-safe
operator recovery around existing agent frameworks.

## v0.4 Scope

v0.4 should make the current runtime safe to explain, test, and operate:

- publish the verification contract
- document the evidence timeline
- document the `TrustedExecutor` boundary
- publish the threat model
- pin certified adapter claims to tests

## v0.5 Scope

v0.5 should expand from certified Python adapters to production integration
paths for the most dangerous competitors:

- Pydantic AI durable-execution sidecar
- Mastra sidecar protocol
- OpenAI Agents SDK guardrail bridge
- Inspect AI evidence export
- reliability benchmark harness

## Pages

- [Verification contract](verification-contract.md)
- [Evidence timeline](evidence-timeline.md)
- [TrustedExecutor](trusted-executor.md)
- [Threat model](threat-model.md)
- [v0.5 roadmap](roadmap-v0.5.md)
