# Releasing Veridian

This project uses evidence-based releases. Every public release post must
include a completed release evidence block.

## Why this file is prescriptive

Between April and August 2026 the repository advanced from 0.1.0 to 0.4.0 while
PyPI still served 0.1.0. The v0.2.0 publish job failed three times and nobody
noticed for four months; 0.3.0 and 0.4.0 never had a GitHub Release cut, so the
release-triggered workflow never ran at all. The Definition of Done below
already said "if mismatch exists, do not mark release complete" — the rule was
correct and went unenforced.

Two things changed as a result: publishing is now dry-run first, and a scheduled
parity check fails loudly whenever the newest tag and PyPI latest diverge.

## Required inputs

- Clean `main` branch, CI green on the exact commit being released.
- Version bumped in `pyproject.toml` and `veridian/__init__.py`.
- `CHANGELOG.md` updated.
- **PyPI trusted publishing configured** for `veridian-ai`, and a GitHub
  environment named `pypi`. The workflow authenticates by exchanging a GitHub
  OIDC token for a short-lived PyPI credential — there is **no**
  `PYPI_API_TOKEN` secret, and one must not be reintroduced.

Verify trusted publishing before tagging:
<https://pypi.org/manage/project/veridian-ai/settings/publishing/>

## Release steps

### 1. Run the gates locally

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy veridian --strict
uv run pytest -q --tb=short
uv run pytest --cov=veridian --cov-fail-under=85 -q
```

### 2. Build and validate artifacts

```bash
uv build
uv run --with twine python -m twine check dist/*.whl dist/*.tar.gz
python scripts/check_package_artifacts.py dist/*
```

### 3. Dry-run against TestPyPI

Do this **before** tagging. It exercises the real publish path — build, metadata
validation, OIDC exchange, upload — against a throwaway index, so a
configuration failure surfaces here instead of on a tag that is already public.

Run the `Publish to PyPI` workflow manually with `target: testpypi`:

```
Actions → Publish to PyPI → Run workflow → target: testpypi
```

Requires a TestPyPI trusted publisher and a `testpypi` GitHub environment. If
the dry run fails, fix it before proceeding — a failed dry run is the cheap
version of the v0.2.0 incident.

### 4. Create release notes

- Copy [.github/RELEASE_EVIDENCE_TEMPLATE.md](.github/RELEASE_EVIDENCE_TEMPLATE.md)
- Fill every field.
- Add a claim-to-test mapping for every user-facing claim.

### 5. Tag and publish

- Tag format: `vX.Y.Z`
- Publish the GitHub Release with the completed evidence block. Publishing the
  release triggers the workflow.

### 6. Confirm parity

```bash
python scripts/check_release_parity.py
```

Compares the newest `v*` tag against PyPI latest. Exit `0` means parity; exit
`1` means divergence. It also runs on a schedule so a silent gap is caught
within a day rather than a quarter.

## Definition of done

A release is complete only when **all** hold:

- [ ] The GitHub tag/release version matches PyPI latest.
- [ ] `scripts/check_release_parity.py` exits `0`.
- [ ] The evidence block is present and complete.
- [ ] The CI publish job succeeded.
- [ ] `pip install veridian-ai==X.Y.Z` in a clean environment imports and runs
      `veridian --help`.

If any box is unchecked, the release is not done. Do not announce it.

## If the publish job fails

1. **Do not delete and re-tag.** Fix forward with a patch version.
2. Capture the job log immediately — GitHub log retention expires and the
   v0.2.0 failure cause is now permanently unrecoverable.
3. Reproduce against TestPyPI before retrying against PyPI.
4. Record the cause in `CHANGELOG.md` so the next person inherits the knowledge
   rather than the surprise.
