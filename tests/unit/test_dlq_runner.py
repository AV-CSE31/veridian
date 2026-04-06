"""
tests.unit.test_dlq_runner
───────────────────────────
Audit F1: DLQ is wired into runner failure path on ABANDONED.

Proves:
- Abandoned tasks are enqueued into DLQ with triage category + metadata.
- Non-abandoned tasks (FAILED → PENDING retry) are NOT enqueued.
- DLQ=None (opt-out) is a silent no-op.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.dlq import DeadLetterQueue, TriageCategory
from veridian.core.task import Task
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider


def _bad_provider() -> MockProvider:
    """Provider that returns output missing required schema fields."""
    p = MockProvider()
    for _ in range(20):
        # Schema verifier requires "summary" — this output lacks it → fail
        p.script_veridian_result({"wrong": "nope"})
    return p


@pytest.fixture
def env(tmp_path: Path):
    config = VeridianConfig(
        max_turns_per_task=2,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )
    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    dlq = DeadLetterQueue(storage_path=tmp_path / "dlq.json", max_retries=5)
    return config, ledger, dlq


class TestDLQOnAbandoned:
    def test_abandoned_task_lands_in_dlq(self, env):
        config, ledger, dlq = env
        # max_retries=0 means first failure → ABANDONED immediately
        task = Task(
            title="will_abandon",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary"]},
            max_retries=0,
        )
        ledger.add([task])

        runner = VeridianRunner(
            ledger=ledger,
            provider=_bad_provider(),
            config=config,
            hooks=HookRegistry(),
            dlq=dlq,
        )
        summary = runner.run()

        assert summary.abandoned_count == 1
        assert dlq.size() == 1
        entry = dlq.get(task.id)
        assert entry is not None
        assert entry.task_id == task.id
        assert entry.triage_category in (
            TriageCategory.PERMANENT,
            TriageCategory.UNKNOWN,
        )
        assert entry.retry_count >= 1

    def test_failed_but_retryable_task_does_not_enter_dlq(self, env):
        config, ledger, dlq = env
        task = Task(
            title="will_retry",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary"]},
            max_retries=5,  # high limit — stays FAILED, never ABANDONED
        )
        ledger.add([task])

        runner = VeridianRunner(
            ledger=ledger,
            provider=_bad_provider(),
            config=config,
            hooks=HookRegistry(),
            dlq=dlq,
        )
        runner.run()
        # Task stays in FAILED (not ABANDONED) because max_retries is high
        assert dlq.size() == 0

    def test_no_dlq_attached_is_silent_no_op(self, env):
        config, ledger, _ = env
        task = Task(
            title="will_abandon_silently",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary"]},
            max_retries=0,
        )
        ledger.add([task])

        # dlq=None — the default
        runner = VeridianRunner(
            ledger=ledger,
            provider=_bad_provider(),
            config=config,
            hooks=HookRegistry(),
        )
        summary = runner.run()
        assert summary.abandoned_count == 1
        # No crash, no DLQ side effects
