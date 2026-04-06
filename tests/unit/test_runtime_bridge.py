"""Unit tests for RuntimeStoreBridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.exceptions import TaskAlreadyClaimed, TaskNotFound, TaskNotPaused
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.loop.runtime_store import RuntimeStore
from veridian.storage.local_json import LocalJSONStorage
from veridian.storage.runtime_bridge import RuntimeStoreBridge


@pytest.fixture()
def bridge(tmp_path: Path) -> RuntimeStoreBridge:
    storage = LocalJSONStorage(tmp_path / "runtime_bridge_store.json")
    return RuntimeStoreBridge(storage)


def _task(task_id: str, *, status: TaskStatus = TaskStatus.PENDING, priority: int = 50) -> Task:
    return Task(id=task_id, title=f"task {task_id}", status=status, priority=priority)


def test_bridge_is_runtime_store_protocol(bridge: RuntimeStoreBridge) -> None:
    assert isinstance(bridge, RuntimeStore)


def test_add_and_get_roundtrip(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    assert bridge.get("t1").id == "t1"


def test_get_next_includes_paused_first(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("paused"), _task("pending", priority=99)])
    bridge.claim("paused", "runner-a")
    bridge.pause("paused", reason="review")
    nxt = bridge.get_next(include_paused=True)
    assert nxt is not None
    assert nxt.id == "paused"
    assert nxt.status == TaskStatus.PAUSED


def test_claim_sets_owner_and_prevents_other_runner(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    claimed = bridge.claim("t1", "runner-a")
    assert claimed.claimed_by == "runner-a"
    with pytest.raises(TaskAlreadyClaimed):
        bridge.claim("t1", "runner-b")


def test_submit_and_mark_done(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    bridge.claim("t1", "runner-a")
    result = TaskResult(raw_output="ok")
    verifying = bridge.submit_result("t1", result)
    assert verifying.status == TaskStatus.VERIFYING
    done = bridge.mark_done("t1", result)
    assert done.status == TaskStatus.DONE
    assert done.result is not None
    assert done.result.verified is True


def test_checkpoint_result_keeps_status(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    bridge.claim("t1", "runner-a")
    updated = bridge.checkpoint_result("t1", TaskResult(raw_output="partial"))
    assert updated.status == TaskStatus.IN_PROGRESS


def test_mark_failed_abandons_when_retry_budget_exceeded(bridge: RuntimeStoreBridge) -> None:
    t = _task("t1")
    t.max_retries = 0
    bridge.add([t])
    bridge.claim("t1", "runner-a")
    failed = bridge.mark_failed("t1", "boom")
    assert failed.status == TaskStatus.ABANDONED
    assert failed.retry_count == 1


def test_pause_resume_payload_roundtrip(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    bridge.claim("t1", "runner-a")
    bridge.pause("t1", reason="need-human", payload={"cursor": {"turn": 3}})
    resumed = bridge.resume("t1", "runner-b")
    assert resumed.status == TaskStatus.IN_PROGRESS
    assert resumed.result is not None
    payload = resumed.result.extras.get("pause_payload", {})
    assert payload.get("cursor") == {"turn": 3}
    assert payload.get("resume_count") == 1


def test_resume_non_paused_raises(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    with pytest.raises(TaskNotPaused):
        bridge.resume("t1", "runner-a")


def test_reset_in_progress_preserves_paused(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("a"), _task("b")])
    bridge.claim("a", "runner-a")
    bridge.claim("b", "runner-a")
    bridge.pause("b", reason="hold")
    reset = bridge.reset_in_progress()
    assert reset == 1
    assert bridge.get("a").status == TaskStatus.PENDING
    assert bridge.get("b").status == TaskStatus.PAUSED


def test_add_skip_duplicates(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    assert bridge.add([_task("t1")], skip_duplicates=True) == 0
    assert bridge.add([_task("t1")], skip_duplicates=False) == 1


def test_missing_task_raises(bridge: RuntimeStoreBridge) -> None:
    with pytest.raises(TaskNotFound):
        bridge.get("missing")
