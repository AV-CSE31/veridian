# v0.5 Production Roadmap

This roadmap stops Veridian from competing as a general agent framework.
The product focus is verified completion and evidence for existing agent
runtimes.

## v0.4: Make The Current Contract Production-Readable

Required before v0.4:

- publish the verification contract
- publish the evidence timeline contract
- publish the `TrustedExecutor` boundary
- publish the threat model
- pin integration claims with tests
- keep LangGraph and CrewAI as the only certified adapters

Do not add broad platform, memory, or self-evolution promises to public
positioning before this lands.

## v0.5: Expand The Integration Boundary

Required before v0.5:

- Pydantic AI durable-execution sidecar spike
- Mastra sidecar protocol spike
- OpenAI Agents SDK guardrail bridge
- Inspect AI evidence export
- reliability benchmark harness

These can ship as preview integrations if the docs clearly label support level,
tested versions, failure behavior, and known gaps.

## Stop Doing

Delay these until the verification substrate is undeniable:

- marketing Veridian as a full agent framework
- adding more agent abstractions without adapter demand
- expanding self-evolution features as the headline
- claiming sandboxing without host isolation
- claiming certified framework support without tests

## Release Gate

Every v0.5 integration page must include:

- support level
- tested versions
- verification boundary
- failure behavior
- evidence location
- security boundary
- smoke or certification test path
