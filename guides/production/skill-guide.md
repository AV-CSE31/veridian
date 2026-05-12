# Skill Library

`SkillLibrary` is Veridian's verified procedural memory: it extracts reusable
procedures from completed-and-verified tasks and surfaces them to the
`InitializerAgent` on future runs.

**Invariant:** skills are only ever extracted from `TaskStatus.DONE` tasks
that passed verification. Unverified completions are never stored. This makes
the library safe to consult — every skill in it has at least one passing
verifier-backed precedent.

## Enable

Skill extraction is opt-in via `VeridianConfig`:

```python
from veridian import VeridianConfig, VeridianRunner

config = VeridianConfig(
    skill_library_path="ops/skills.json",
    skill_min_confidence=0.70,    # admission threshold (0.0–1.0)
    skill_max_retries=1,          # max retries allowed for a skill candidate
)

runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
summary = runner.run()
```

When `skill_library_path` is set, `VeridianRunner` constructs a `SkillLibrary`
automatically and calls `post_run()` after every successful run. The runner
also exposes `query()` to the `InitializerAgent` so prior skills can prime
the next worker prompt.

## Manual construction

For tests or custom embedding functions, pass a `SkillLibrary` directly:

```python
from veridian.skills.library import SkillLibrary

skills = SkillLibrary(
    store_path="ops/skills.json",
    provider=provider,
    min_confidence=0.70,
    max_retries_for_skill=1,
    embed_fn=my_embedding_fn,    # optional: callable(text) → list[float]
)

runner = VeridianRunner(
    ledger=ledger,
    provider=provider,
    skill_library=skills,
)
```

## Admission control

Each candidate skill passes through `SkillAdmissionControl` before being
written to the store. Rejection reasons are logged at DEBUG level. A candidate
is admitted only if:

1. it originates from a verified-DONE task,
2. its confidence ≥ `min_confidence`,
3. the task's `retry_count` ≤ `max_retries_for_skill`,
4. it is not a near-duplicate of an existing skill (embedding similarity).

## Querying

```python
candidates = skills.query("write a postgres migration", top_k=3)
for skill in candidates:
    print(f"{skill.id}: {skill.title} (uses: {skill.use_count})")
```

The `InitializerAgent` does this automatically when the runner has a skill
library configured. Surfaced skills are injected into the prompt as
**reference precedents**, not as authoritative instructions.

## Blast-radius and quarantine

When a skill produces a verified-FAIL downstream — i.e. an agent followed a
skill but the run did not pass — `BlastRadiusAnalyzer` and `SkillQuarantine`
can isolate the offending skill:

```python
from veridian.skills.quarantine import SkillQuarantine

quarantine = SkillQuarantine(store=skills._store)
quarantine.quarantine_by_id(skill_id="abc123", reason="caused 3 downstream FAIL")
```

Quarantined skills remain in the store but are skipped by `query()`. Reverse
with `release_by_id()` once the regression is understood.

## What the library does NOT do

- It does not modify task state or verifier output.
- It does not run skills automatically — they are surfaced as references.
- It does not embed remote calls; if `embed_fn` is None the store uses a
  lightweight in-process hash embedding suitable for testing only.

For production retrieval quality, plug in a real embedding model.

## When NOT to use the skill library

- One-shot scripts with no expected reuse.
- Pipelines where verifier passes are not yet trustworthy — admitting from
  weak verifiers contaminates the library.
- Strict replay-compat runs where retrieval-based prompt variability would
  cause drift signals (see [drift guide](drift-guide.md)).

## Related

- [Verification contract](verification-contract.md) — the DONE invariant
- [Drift guide](drift-guide.md) — paired diagnostic for regressions
- [`veridian.skills.library`](../../veridian/skills/library.py)
- [`veridian.skills.admission`](../../veridian/skills/admission.py)
- [`veridian.skills.quarantine`](../../veridian/skills/quarantine.py)
