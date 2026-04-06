"""
tests.integration.test_activity_journal_runner
───────────────────────────────────────────────
RV3-004/005 end-to-end: runner persists the activity journal in TaskResult
extras and a resumed run returns cached LLM results without re-calling the
provider. Enforces the Temporal-style zero-duplicate-side-effect invariant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider

_SCHEMA_CONFIG = {"required_fields": ["summary"]}


class _CountingProvider(MockProvider):
    """MockProvider that records every .complete() call so tests can assert
    no duplicate side-effects across restart."""

    def __init__(self) -> None:
        super().__init__()
        self.counter = 0
        self.model = "mock/v1"  # exposed for replay snapshot

    def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:  # type: ignore[override]
        self.counter += 1
        return LLMResponse(
            content=(
                "<veridian:result>\n"
                '{"summary": "done", "structured": {"summary": "done"}, "artifacts": []}\n'
                "</veridian:result>"
            ),
            input_tokens=200,
            output_tokens=50,
            model=self.model,
        )


@pytest.fixture
def config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        activity_journal_enabled=True,
    )


def _make_task() -> Task:
    return Task(title="t1", verifier_id="schema", verifier_config=_SCHEMA_CONFIG)


class TestActivityJournalPersistedOnSuccess:
    def test_successful_run_writes_activity_journal_to_extras(self, config: VeridianConfig) -> None:
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        task = _make_task()
        ledger.add([task])
        provider = _CountingProvider()
        VeridianRunner(
            ledger=ledger,
            provider=provider,
            config=config,
            hooks=HookRegistry(),
        ).run()

        stored = ledger.get(task.id)
        assert stored.status == TaskStatus.DONE
        journal_data = stored.result.extras.get("activity_journal")
        assert journal_data is not None
        assert isinstance(journal_data, list)
        # At least one LLM activity should be recorded
        assert any(r.get("fn_name", "").endswith("complete") for r in journal_data)
        # Each entry should carry both activity_id and idempotency_key
        for r in journal_data:
            assert r.get("activity_id")
            assert r.get("idempotency_key")


class TestReplayReusesJournaledResults:
    def test_resumed_task_does_not_re_call_provider(self, config: VeridianConfig) -> None:
        """Core RV3-004 acceptance: on resume, recorded activity outputs are
        returned instead of re-executing the side effect."""
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        task = _make_task()
        ledger.add([task])

        provider_a = _CountingProvider()
        VeridianRunner(
            ledger=ledger,
            provider=provider_a,
            config=config,
            hooks=HookRegistry(),
        ).run()
        first_run_calls = provider_a.counter
        assert first_run_calls >= 1

        # Simulate an operator retry: reset the task to PENDING so the runner
        # picks it up again, preserving the result (and the journal).
        done_task = ledger.get(task.id)
        done_task.status = TaskStatus.PENDING
        done_task.claimed_by = None
        ledger.add([done_task], skip_duplicates=False)

        provider_b = _CountingProvider()
        VeridianRunner(
            ledger=ledger,
            provider=provider_b,
            config=config,
            hooks=HookRegistry(),
        ).run()

        # Provider B must NOT have been invoked for activities that were
        # already journaled in run A.
        assert provider_b.call_count == 0, (
            f"Activity journal replay failed: provider was called "
            f"{provider_b.call_count} times on resume (expected 0)"
        )
        assert ledger.get(task.id).status == TaskStatus.DONE
