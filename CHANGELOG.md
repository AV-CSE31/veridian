# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the caveat that
the pre-1.0 line makes no stability guarantee: minor versions have changed the
public surface substantially and will continue to until 1.0.

## Distribution history — read this first

The repository and PyPI diverged for five months. This table is the honest
record:

| Version | Tagged | Published to PyPI | Note |
|---|---|---|---|
| 0.1.0 | 2026-03-24 | **Yes**, 2026-03-24 | The only version on PyPI before 0.4.0 |
| 0.2.0 | 2026-04-08 | **No** — publish workflow failed | Release existed on GitHub; the job failed three times and the failure logs have since expired |
| 0.3.0 | 2026-04-23 | **No** | The tag exists, but no GitHub Release was published, and the workflow triggers on `release: published` — so it never ran |
| 0.4.0 | — | **No** | Never tagged and never released |

Anyone who ran `pip install veridian-ai` between April and August 2026 received
**0.1.0** — a snapshot of the earlier platform architecture, not the library
this repository documents. If you have a `veridian-ai==0.1.0` pin, treat an
upgrade as a migration to a different library, not a version bump.

A scheduled parity check now fails loudly whenever the newest tag and the PyPI
latest version diverge, so this cannot recur silently.

## [Unreleased]

### Added

- **`veridian.gate` — a composed front door over the assurance kernel.**
  `Gate`, the `@check` decorator and the `@gate.guard(...)` decorator reduce the
  minimal permit-and-receipt flow from roughly seventeen hand-constructed
  dataclasses to about twenty lines. The porcelain introduces no new trust
  properties: every artifact it emits is a plain `veridian.assurance` /
  `veridian.effects` value that the existing offline verifier accepts.
- Gate checks bind **verifier implementation identity** — module, qualified
  name, declared version, canonical configuration, source digest and runtime —
  into each clause's `VerifierManifestV1`. Changing a predicate body changes the
  clause's manifest digest. Where source is unreachable the manifest records
  `source_bound: false` rather than claiming a binding it does not have.
  (Partial coverage of #20 for gate-defined checks; the standard runner and
  report path still needs the same treatment.)
- Public documentation subtree under `docs/`: quickstart, threat model, a frozen
  proof-format specification, and mapping notes for EU AI Act Article 12 and the
  Open Agent Passport / AP2 vocabularies.
- `CONTRIBUTING.md`, and this changelog.
- `examples/gate_quickstart.py` — runs offline, asserts its own claims.
- A scheduled **PyPI parity check** workflow, and a `workflow_dispatch`
  TestPyPI dry-run path on the publish workflow.

### Changed

- `veridian.__all__` grew from 24 to 33 names to expose the gate porcelain at
  the top level. Before this, `import veridian` showed only the task-runner era
  surface while the assurance kernel — 54% of the library — was reachable solely
  by submodule path. The API-stability and package-boundary guards were updated
  deliberately, with the reason recorded in each.
- README leads with the permit-and-receipt flow instead of the task runner.
- `RELEASING.md` now describes the OIDC trusted-publishing workflow that
  actually runs. It previously required a `PYPI_API_TOKEN` repository secret
  that the workflow no longer uses — a documented process that could not have
  succeeded as written.
- `.gitignore` no longer excludes `CHANGELOG.md`, and `docs/` is now private by
  default with public files allowlisted individually rather than the whole tree
  being hidden.

### Not changed

- No change to any existing schema, digest derivation, signature envelope or
  verification algorithm. Proof bundles produced before this change verify
  identically after it.

## [0.4.0] — source only, never published

- Proof-carrying assurance runtime: Ed25519 attestation, canonical encoding,
  signed single-use execution permits, revocation, transactional SQLite outbox,
  signed effect receipts.
- Deterministic verification mathematics with counterexamples; synthetic banking
  and deployment packs.
- Protocol adapters for direct/generic Python, OpenAI Responses, MCP, LangGraph
  and a versioned Pydantic AI deferred-tool profile.
- Append-only WAL ledger as the default task-ledger mode.
- Versioned adversarial, concurrency and crash-recovery benchmark harness.

## [0.3.0] — 2026-04-23, tagged but never published

- "Light core" refactor: the dashboard, storage backends, observability, policy
  DSL, audit and self-improvement subsystems were removed, reducing the library
  from roughly 200 modules to a verification-focused core.

## [0.2.0] — 2026-04-01, publish failed

- Verification evidence reports, production hardening, durability fsync,
  path-traversal guard, `/metrics` authentication.

## [0.1.0] — 2026-03-24

- Initial release: deterministic task runner, crash-recoverable ledger,
  verifier registry and completion contracts.
