# Threat Model

Veridian protects the completion boundary of agent workflows. It does not
replace host infrastructure security, identity, secrets management, or network
isolation.

## Assets

Primary assets:

- ledger state
- verifier configuration
- task output
- replay evidence
- trace and proof-chain data
- secrets and PII contained in outputs or traces

## Trust Boundaries

Untrusted or partially trusted inputs:

- model output
- agent-generated code
- tool responses
- framework callbacks
- external web/API responses
- user-provided task descriptions

Trusted components:

- verifier implementations controlled by the application team
- ledger/storage backend
- policy configuration
- host sandbox and deployment environment

## P0 Threats

- model self-certifies task completion
- verifier failures are swallowed by framework code
- task transitions bypass the ledger
- replay evidence is missing after incident recovery
- untrusted commands run without host sandboxing
- secrets or PII leak into exported traces
- adapter docs claim production support without tests

## Required Controls

- only ledger/runtime APIs transition task status
- verifiers are deterministic and version-controlled
- verifier failure behavior is explicit
- traces are filtered before export
- production integrations pin framework versions
- certified adapters have compatibility and failure-path tests
- untrusted tools execute inside a host sandbox

## Out Of Scope

Veridian does not by itself provide:

- cloud IAM
- network egress controls
- secret rotation
- multi-tenant infrastructure isolation
- malware analysis
- full eval suite replacement
