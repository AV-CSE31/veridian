## Summary

<!-- What changed and why? -->

## Verification

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy veridian/ --strict`
- [ ] `pytest -q --tb=short`
- [ ] `pytest --cov=veridian --cov-fail-under=85`

## Production-Grade PR Checklist

- [ ] Does this avoid adding dead code?
- [ ] Does this avoid duplication and preserve module boundaries?
- [ ] Are names clear and domain-correct?
- [ ] Are external inputs validated?
- [ ] Is error handling explicit (no swallowed exceptions)?
- [ ] Are logs useful and free of secrets/PII?
- [ ] Are tests meaningful and stable?
- [ ] Is rollback/failure behavior understood?
- [ ] Are config/env var changes documented?
