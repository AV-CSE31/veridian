# TrustedExecutor

`TrustedExecutor` is a guarded command wrapper and output sanitizer. It is not a complete sandbox boundary.

Use it when you need local command execution with input validation and sanitized
output. Do not use it as the only isolation layer for untrusted code,
third-party tools, or attacker-controlled commands.

## Production Boundary

Production deployments must provide the sandbox boundary outside
`TrustedExecutor` when commands can touch sensitive systems.

Acceptable host controls include:

- container isolation
- locked-down service accounts
- network egress restrictions
- read-only working directories
- resource limits
- allowlisted commands
- temporary workspaces
- secret-free execution environments

## Non-Goals

`TrustedExecutor` does not claim to provide:

- kernel isolation
- container isolation
- full shell escape prevention
- filesystem policy enforcement
- network sandboxing
- tenant isolation by itself

## v0.4 Requirement

All public docs must describe `TrustedExecutor` as a command wrapper, not a
sandbox.

## v0.5 Decision

Before v0.5, choose one path:

- rename it to `GuardedCommandExecutor`
- keep the name but add a prominent warning everywhere
- add a real sandbox backend and reserve `TrustedExecutor` for trusted
  commands only
