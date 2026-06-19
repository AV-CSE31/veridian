"""
tests.unit.test_grader_metadata
---------------------------------------------------------------
Fitment follow-up item 5: every passing or failing verification stamps
verifier identity + config hash + provider model into
``TaskResult.extras['grader_metadata']``. This lets operators detect
rubric drift and tie a given verdict to a specific verifier version.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskStatus
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider


@pytest.fixture
def runner(tmp_path: Path) -> VeridianRunner:
    config = VeridianConfig(
        max_turns_per_task=3,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )
    ledger = TaskLedger(
        path=config.ledger_file,
        progress_file=str(config.progress_file),
    )
    ledger.add(
        [
            Task(
                title="trivial",
                verifier_id="schema",
                verifier_config={"required_fields": ["summary"]},
            )
        ]
    )
    return VeridianRunner(ledger=ledger, provider=MockProvider(), config=config)


def test_grader_metadata_stamped_on_result(runner: VeridianRunner) -> None:
    summary = runner.run()
    assert summary.total_tasks == 1
    tasks = runner.ledger.list()
    assert tasks, "Expected one task in ledger"
    task = tasks[0]
    assert task.status in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.ABANDONED}
    assert task.result is not None
    meta = task.result.extras.get("grader_metadata")
    assert isinstance(meta, dict), f"grader_metadata missing: extras={task.result.extras}"
    assert meta["verifier_id"] == "schema"
    assert "verifier_class" in meta and meta["verifier_class"].startswith("veridian.verify.builtin")
    assert "verifier_config_hash" in meta
    assert len(meta["verifier_config_hash"]) == 16
    assert meta["grader_provider_class"] == "MockProvider"


def test_config_hash_changes_when_config_changes(runner: VeridianRunner, tmp_path: Path) -> None:
    """Two distinct verifier configs produce two distinct hashes."""
    from veridian.loop.task_dispatcher import _TaskDispatcher

    dispatcher: _TaskDispatcher = runner._dispatcher
    t1 = Task(title="a", verifier_id="schema", verifier_config={"required_fields": ["x"]})
    t2 = Task(title="b", verifier_id="schema", verifier_config={"required_fields": ["y"]})
    verifier = runner._verifier_registry.get(t1.verifier_id, t1.verifier_config)
    m1 = dispatcher._grader_metadata(t1, verifier)
    m2 = dispatcher._grader_metadata(t2, verifier)
    assert m1["verifier_config_hash"] != m2["verifier_config_hash"]
