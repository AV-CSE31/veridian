"""Cross-backend parity checks through RuntimeStoreBridge."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.storage.local_json import LocalJSONStorage
from veridian.storage.runtime_bridge import RuntimeStoreBridge


def _redis_available() -> bool:
    if os.getenv("VERIDIAN_TEST_REDIS", "0") != "1":
        return False
    try:
        import redis  # noqa: PLC0415

        redis.Redis(host="localhost", port=6379, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


def _postgres_available() -> bool:
    if os.getenv("VERIDIAN_TEST_POSTGRES", "0") != "1":
        return False
    try:
        import psycopg2  # noqa: PLC0415

        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="veridian",
            password="veridian",
            dbname="veridian_test",
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


def _make_local_json(tmp_path: Path) -> RuntimeStoreBridge:
    return RuntimeStoreBridge(LocalJSONStorage(tmp_path / "parity_local.json"))


def _make_redis(_: Path) -> RuntimeStoreBridge:
    from veridian.storage.redis_backend import RedisStorage  # noqa: PLC0415

    storage = RedisStorage(host="localhost", port=6379, db=15, key_prefix="veridian_test:")
    for t in storage.list_all():
        # isolate test db namespace
        storage._r.delete(storage._task_key(t.id))  # noqa: SLF001
    storage._r.delete(storage._queue_key())  # noqa: SLF001
    return RuntimeStoreBridge(storage)


def _make_postgres(_: Path) -> RuntimeStoreBridge:
    from veridian.storage.postgres_backend import PostgresStorage  # noqa: PLC0415

    storage = PostgresStorage(
        dsn="host=localhost port=5432 user=veridian password=veridian dbname=veridian_test",
        table="veridian_tasks_test",
    )
    with storage._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        cur.execute("DELETE FROM veridian_tasks_test")
        conn.commit()
    return RuntimeStoreBridge(storage)


@pytest.fixture(
    params=[
        pytest.param("local_json", id="local_json"),
        pytest.param(
            "redis",
            id="redis",
            marks=pytest.mark.skipif(not _redis_available(), reason="redis not available"),
        ),
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(not _postgres_available(), reason="postgres not available"),
        ),
    ]
)
def bridge(request: pytest.FixtureRequest, tmp_path: Path) -> RuntimeStoreBridge:
    backend = request.param
    factories: dict[str, Callable[[Path], RuntimeStoreBridge]] = {
        "local_json": _make_local_json,
        "redis": _make_redis,
        "postgres": _make_postgres,
    }
    return factories[backend](tmp_path)


def _task(task_id: str, *, priority: int = 50, phase: str = "default") -> Task:
    return Task(id=task_id, title=f"task {task_id}", priority=priority, phase=phase)


def test_claim_submit_done_lifecycle_parity(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    bridge.claim("t1", "runner-a")
    res = TaskResult(raw_output="ok")
    bridge.submit_result("t1", res)
    done = bridge.mark_done("t1", res)
    assert done.status == TaskStatus.DONE
    assert bridge.get("t1").status == TaskStatus.DONE


def test_pause_resume_parity(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("t1")])
    bridge.claim("t1", "runner-a")
    bridge.pause("t1", reason="review", payload={"cursor": {"turn": 1}})
    paused = bridge.get("t1")
    assert paused.status == TaskStatus.PAUSED
    resumed = bridge.resume("t1", "runner-b")
    assert resumed.status == TaskStatus.IN_PROGRESS
    assert resumed.result is not None
    assert resumed.result.extras.get("pause_payload", {}).get("cursor") == {"turn": 1}


def test_get_next_phase_and_priority_parity(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("a", phase="alpha", priority=10), _task("b", phase="beta", priority=90)])
    nxt = bridge.get_next(phase="beta")
    assert nxt is not None
    assert nxt.id == "b"


def test_reset_in_progress_parity(bridge: RuntimeStoreBridge) -> None:
    bridge.add([_task("a"), _task("b")])
    bridge.claim("a", "runner-a")
    bridge.claim("b", "runner-a")
    bridge.pause("b", reason="hold")
    count = bridge.reset_in_progress()
    assert count == 1
    assert bridge.get("a").status == TaskStatus.PENDING
    assert bridge.get("b").status == TaskStatus.PAUSED
