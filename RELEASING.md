# Releasing Veridian

This project uses evidence-based releases.
Every public release post must include a completed release evidence block.

## Required Inputs

- Clean `main` branch
- Version bumped in `pyproject.toml` and `veridian/__init__.py`
- `PYPI_API_TOKEN` configured in GitHub repository secret

## Release Steps

1. Run gates locally:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy veridian/ --strict
uv run pytest -q --tb=short
uv run pytest --cov=veridian --cov-fail-under=85 -q
```

2. Build and validate package artifacts:

```bash
uv build
uv run --with twine python -m twine check dist/*.whl dist/*.tar.gz
```

3. Create release notes using the template:

- Copy [.github/RELEASE_EVIDENCE_TEMPLATE.md](.github/RELEASE_EVIDENCE_TEMPLATE.md)
- Fill every field
- Add claim-to-test mapping for all user-facing claims

4. Tag and publish GitHub release:

- Tag format: `vX.Y.Z`
- Publish release notes with completed evidence block

5. Confirm PyPI parity:

- Verify latest at [PyPI](https://pypi.org/project/veridian-ai/) matches the release tag
- If mismatch exists, do not mark release complete

## Definition of Done

A release is complete only when:

- GitHub tag/release version matches PyPI latest
- Evidence block is present and complete
- CI publish job succeeds
