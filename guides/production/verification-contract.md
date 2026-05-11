# Verification Contract

The Veridian verification contract is:

> A task must not transition to `DONE` unless an independent verifier accepts
> the task result.

The model, agent, framework, or tool that produced the result must not be the
authority that marks the work complete.

## Required Inputs

Every production verifier invocation should have:

- task id
- task title and description
- verifier id
- verifier configuration
- raw output or structured output
- run id or framework execution id
- model/provider metadata when available
- policy version when applicable

## Required Output

A verifier must return a deterministic decision:

- `passed`: boolean
- `error`: empty on pass, actionable on failure
- `metadata`: optional evidence values, hashes, spans, and policy notes

Verifier output should be safe to persist in audit logs after secrets and PII
filters have run.

## Determinism Rules

Production verifiers should be deterministic by default:

- same task, output, verifier id, and verifier config should produce the same
  pass/fail decision
- external network calls should be versioned or cached
- LLM judge verifiers must not be the only verifier for irreversible actions
- verifier configuration must be version-controlled

## Composition Rules

Current built-ins support `composite` and `any_of`. Production chains should
declare:

- whether a verifier is fatal or advisory
- whether failures are retryable
- whether human approval can override the failure
- whether the chain uses `all`, `any`, or quorum semantics

Until richer composition is added, production docs should avoid implying
quorum, severity levels, or streaming verifier decisions exist.

## Completion Semantics

On verifier pass, the runner may mark the task `DONE` and stamp the result as
verified.

On verifier failure, the integration must choose one behavior:

- raise to the host framework
- retry with bounded attempts
- pause for human review
- move to DLQ/operator triage
- mark failed or abandoned

Silent verifier failures are not production-supported.
