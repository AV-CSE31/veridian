# Contributing to Veridian

Veridian is a security-critical library maintained by one person. That shapes
what contributions are useful: a small, well-tested change with a clear
rationale is worth more here than a large one, and a change that reduces the
surface is worth more than one that grows it.

## Before you start

For anything beyond a typo, **open an issue first**. The scope boundary is
narrow and deliberate — see [Current Boundaries](README.md#current-boundaries)
and [docs/threat-model.md](docs/threat-model.md#deliberate-non-goals). A patch
that implements a non-goal will be declined however good it is, and it is better
to learn that before writing it.

Deliberately out of scope: a managed control plane, a distributed ledger, a
hosted reviewer, a general policy DSL, an identity issuer, a production payment
connector, an OS-level sandbox.

## Development

```bash
uv sync --extra dev            # or: pip install -e ".[dev]"
uv run ruff check .
uv run ruff format --check .
uv run mypy veridian --strict
uv run pytest -q
uv run pytest --cov=veridian --cov-fail-under=85 -q
```

All five must pass. CI runs them across Linux, macOS and Windows on Python
3.11–3.13, plus a minimal-wheel install, the composite Action and the container.

Run the suite on a **minimal** install too (`.[dev]` with no extras). Several
past CI failures were module-level imports of optional dependencies that only
surfaced without the extras installed.

## What a good change looks like

**Tests state the property, not the implementation.** Name them for the
behaviour a caller relies on: `test_unknown_hard_clause_holds_rather_than_allowing`,
not `test_evaluate_3`. If a test would still pass after the property is broken,
it is not testing the property.

**Fail closed.** Uncertainty must never become permission. A check that raises,
times out, or cannot reach its input produces `HOLD` — never `ALLOW`. If you are
adding a code path that could swallow an error, make it a denial.

**Never widen a claim.** This project's most valuable asset is that its
documentation does not overstate what it proves. Concretely:

- a signature is not freshness;
- an unanchored chain is not append-only history;
- a bounded subprocess is not a sandbox;
- passing benchmarks are evidence for the disclosed cases only;
- "supports an obligation" is not "achieves compliance".

If a change makes a stronger guarantee available, say exactly what it now
guarantees and what it still does not. If it does not, do not let the prose
imply it does.

**Schemas are frozen.** Do not redefine an existing `schema_id`. Additive
changes take a new one. A proof bundle written against a published schema must
keep verifying. See [docs/proof-format.md](docs/proof-format.md#stability).

**Growing the public surface is deliberate.** `tests/unit/test_api_stability.py`
and `tests/unit/test_package_boundary.py` pin `veridian.__all__`, the top-level
package set and the examples list. They exist to make additions a decision, not
an accident. If your change should grow the surface, update those tests **and
record why in the test file** — see the gate porcelain entries for the shape.

## Commits and pull requests

- One logical change per PR. If the description needs "and also", split it.
- Explain **why**, not what — the diff already says what.
- State what you ran and what the result was. If something is untested, say so.
- Link the issue.

## Security

Do not open a public issue for a vulnerability. Use GitHub's private
vulnerability reporting; see [SECURITY.md](SECURITY.md). Never include exploit
details, credentials, real proof bundles or production data.

## Provenance

A substantial share of this codebase was written with AI assistance, and that is
disclosed rather than hidden. If you use an AI tool, the same bar applies: you
are the author, you are accountable for every line, and "the model wrote it" is
not a review. Verify that generated claims about behaviour match the code — the
most common failure mode is confident prose describing a guarantee the
implementation does not make.

## Licence

Contributions are accepted under the [MIT Licence](LICENSE).
