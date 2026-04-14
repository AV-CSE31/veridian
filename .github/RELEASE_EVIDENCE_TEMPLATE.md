## Release Evidence Block

Use this block in every public GitHub release post.
Do not publish a release note without completing all fields.

### Metadata

- Version: `vX.Y.Z`
- Date (UTC): `YYYY-MM-DD`
- Commit: `<full_sha>`
- PyPI package: `veridian-ai==X.Y.Z`
- Release owner: `@handle`

### Quality Gates

- [ ] `ruff check .` (pass)
- [ ] `ruff format --check .` (pass)
- [ ] `mypy veridian/ --strict` (pass)
- [ ] `pytest -q --tb=short` (pass)
- [ ] `pytest --cov=veridian --cov-fail-under=85 -q` (pass, include coverage %)

### Test Summary

- Total tests passed: `<n>`
- Tests skipped: `<n>`
- Failures: `0`
- Key integration suites executed:
  - [ ] `tests/integration/test_pause_resume.py`
  - [ ] `tests/integration/test_activity_journal_runner.py`
  - [ ] `tests/integration/test_replay_compat_runner.py`
  - [ ] `tests/integration/test_parallel_parity.py`
  - [ ] Adapter certification suites (LangGraph/CrewAI/matrix)

### Packaging Evidence

- [ ] `uv build` produced:
  - `dist/veridian_ai-X.Y.Z.tar.gz`
  - `dist/veridian_ai-X.Y.Z-py3-none-any.whl`
- [ ] `uv run --with twine python -m twine check dist/*.whl dist/*.tar.gz` (pass)
- SHA256:
  - `veridian_ai-X.Y.Z.tar.gz`: `<sha256>`
  - `veridian_ai-X.Y.Z-py3-none-any.whl`: `<sha256>`

### Claim-to-Test Mapping

List each release claim and its validating test(s):

| Claim | Test file(s) |
|---|---|
| `<claim 1>` | `<tests/...>` |
| `<claim 2>` | `<tests/...>` |

### Compatibility and Migration

- Compatibility matrix updated: [ ] Yes [ ] N/A
- Migration notes updated: [ ] Yes [ ] N/A
- Breaking changes: [ ] None [ ] Present (link migration section)

### Sign-off

- [ ] Release note reviewed by maintainer
- [ ] Evidence block verified against CI run logs
- [ ] PyPI latest version matches release tag
