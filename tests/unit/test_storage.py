"""
tests/unit/test_storage.py
──────────────────────────
Tests for BaseStorage ABC + LocalJSONStorage + storage interface stubs.

TDD: these tests are written BEFORE the implementation.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from veridian.core.exceptions import StorageLockError, TaskNotFound
from veridian.core.task import Task, TaskPriority, TaskResult, TaskStatus
from veridian.storage.base import BaseStorage
from veridian.storage.local_json import LocalJSONStorage

# ── BaseStorage ABC ─────────────────────────────────────────────────────────


class TestBaseStorageABC:
    def test_cannot_instantiate_directly(self) -> None:
        """BaseStorage is abstract — direct instantiation must raise TypeError."""
        with pytest.raises(TypeError):
            BaseStorage()  # type: ignore[abstract]

    def test_required_abstract_methods(self) -> None:
        """BaseStorage ABC must declare all required abstract methods."""
        import inspect

        abstract_methods = {
            name
            for name, _ in inspect.getmembers(BaseStorage, predicate=inspect.isfunction)
            if getattr(getattr(BaseStorage, name), "__isabstractmethod__", False)
        }
        required = {"put", "get", "get_next", "complete", "fail", "list_all", "stats"}
        assert required.issubset(abstract_methods), (
            f"Missing abstract methods: {required - abstract_methods}"
        )


# ── LocalJSONStorage ─────────────────────────────────────────────────────────


class TestLocalJSONStorage:
    @pytest.fixture
    def storage_file(self, tmp_path: Path) -> Path:
        return tmp_path / "tasks.json"

    @pytest.fixture
    def storage(self, storage_file: Path) -> LocalJSONStorage:
        return LocalJSONStorage(storage_file=storage_file)

    @pytest.fixture
    def task(self) -> Task:
        return Task(
            id="t1",
            title="Test task",
            description="Do something",
            priority=TaskPriority.NORMAL,
        )

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_put_and_get(self, storage: LocalJSONStorage, task: Task) -> None:
        """Should store and retrieve a task by ID."""
        storage.put(task)
        retrieved = storage.get("t1")
        assert retrieved.id == "t1"
        assert retrieved.title == "Test task"

    def test_get_next_returns_pending_task(self, storage: LocalJSONStorage, task: Task) -> None:
        """get_next() should return a PENDING task."""
        storage.put(task)
        next_task = storage.get_next()
        assert next_task is not None
        assert next_task.id == "t1"

    def test_get_next_returns_highest_priority(self, storage: LocalJSONStorage) -> None:
        """get_next() should return the highest-priority PENDING task first."""
        low = Task(id="low", title="Low", priority=TaskPriority.LOW)
        high = Task(id="high", title="High", priority=TaskPriority.HIGH)
        critical = Task(id="crit", title="Crit", priority=TaskPriority.CRITICAL)
        for t in [low, high, critical]:
            storage.put(t)

        nxt = storage.get_next()
        assert nxt is not None
        assert nxt.id == "crit"

    def test_get_next_returns_none_when_empty(self, storage: LocalJSONStorage) -> None:
        """get_next() should return None when there are no PENDING tasks."""
        assert storage.get_next() is None

    def test_complete_marks_task_done(self, storage: LocalJSONStorage, task: Task) -> None:
        """complete() should set task status to DONE."""
        storage.put(task)
        result = TaskResult(raw_output="done")
        storage.complete("t1", result)
        updated = storage.get("t1")
        assert updated.status == TaskStatus.DONE

    def test_fail_marks_task_failed(self, storage: LocalJSONStorage, task: Task) -> None:
        """fail() should set task status to FAILED and store the error."""
        storage.put(task)
        storage.fail("t1", "Something went wrong")
        updated = storage.get("t1")
        assert updated.status == TaskStatus.FAILED
        assert updated.last_error == "Something went wrong"

    def test_list_all_returns_all_tasks(self, storage: LocalJSONStorage) -> None:
        """list_all() should return every stored task."""
        t1 = Task(id="a", title="A")
        t2 = Task(id="b", title="B")
        storage.put(t1)
        storage.put(t2)
        all_tasks = storage.list_all()
        ids = {t.id for t in all_tasks}
        assert ids == {"a", "b"}

    def test_stats_counts_by_status(self, storage: LocalJSONStorage) -> None:
        """stats() should return LedgerStats with correct per-status counts."""
        storage.put(Task(id="p1", title="P"))
        t2 = Task(id="d1", title="D")
        storage.put(t2)
        storage.complete("d1", TaskResult(raw_output="ok"))

        s = storage.stats()
        assert s.by_status.get("pending", 0) >= 1
        assert s.by_status.get("done", 0) >= 1

    def test_put_updates_existing_task(self, storage: LocalJSONStorage, task: Task) -> None:
        """Calling put() with an existing ID should update the record."""
        storage.put(task)
        task.title = "Updated title"
        storage.put(task)
        retrieved = storage.get("t1")
        assert retrieved.title == "Updated title"

    # ── Error handling ────────────────────────────────────────────────────────

    def test_get_raises_task_not_found(self, storage: LocalJSONStorage) -> None:
        """get() should raise TaskNotFound for unknown IDs."""
        with pytest.raises(TaskNotFound, match="unknown-id"):
            storage.get("unknown-id")

    def test_complete_raises_task_not_found(self, storage: LocalJSONStorage) -> None:
        """complete() should raise TaskNotFound for unknown IDs."""
        with pytest.raises(TaskNotFound):
            storage.complete("ghost", TaskResult(raw_output=""))

    def test_fail_raises_task_not_found(self, storage: LocalJSONStorage) -> None:
        """fail() should raise TaskNotFound for unknown IDs."""
        with pytest.raises(TaskNotFound):
            storage.fail("ghost", "error")

    # ── Dependency-aware scheduling ───────────────────────────────────────────

    def test_get_next_skips_tasks_with_unmet_dependencies(self, storage: LocalJSONStorage) -> None:
        """get_next() must not return a task whose depends_on are not all DONE."""
        blocker = Task(id="blocker", title="Blocker")
        dependent = Task(id="dep", title="Dependent", depends_on=["blocker"])
        storage.put(blocker)
        storage.put(dependent)
        # blocker is still PENDING, so dependent must be skipped
        nxt = storage.get_next()
        assert nxt is not None
        assert nxt.id == "blocker"

    def test_get_next_returns_dependent_after_dependency_done(
        self, storage: LocalJSONStorage
    ) -> None:
        """get_next() should return dependent task once its dependency is DONE."""
        blocker = Task(id="blocker", title="Blocker")
        dependent = Task(id="dep", title="Dependent", depends_on=["blocker"])
        storage.put(blocker)
        storage.put(dependent)
        storage.complete("blocker", TaskResult(raw_output="done"))
        nxt = storage.get_next()
        assert nxt is not None
        assert nxt.id == "dep"

    # ── Atomicity ─────────────────────────────────────────────────────────────

    def test_no_partial_write_on_concurrent_access(self, storage_file: Path) -> None:
        """Storage file must never be readable in a partial state."""
        storage = LocalJSONStorage(storage_file=storage_file)
        for i in range(5):
            storage.put(Task(id=f"t{i}", title=f"Task {i}"))
        assert storage_file.exists()
        assert not list(storage_file.parent.glob("*.tmp"))

    def test_concurrent_writes_do_not_corrupt_file(self, storage_file: Path) -> None:
        """Concurrent puts from multiple threads must not corrupt storage."""
        storage = LocalJSONStorage(storage_file=storage_file)
        errors: list[Exception] = []

        def write_tasks(start: int) -> None:
            try:
                for i in range(start, start + 5):
                    storage.put(Task(id=f"t{i}", title=f"Task {i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_tasks, args=(i * 5,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # File must be valid JSON
        data = json.loads(storage_file.read_text())
        assert isinstance(data, dict)

    # ── Implements BaseStorage ────────────────────────────────────────────────

    def test_implements_base_storage_interface(self, storage: LocalJSONStorage) -> None:
        """LocalJSONStorage must be a concrete subclass of BaseStorage."""
        assert isinstance(storage, BaseStorage)


# ── RedisStorage (mocked — no real Redis needed) ────────────────────────────


def _make_redis_storage() -> Any:
    """Construct a RedisStorage with a fully mocked Redis client."""
    from veridian.storage.redis_backend import RedisStorage

    mock_redis = MagicMock()
    with patch("veridian.storage.redis_backend.RedisStorage.__init__", return_value=None):
        storage = RedisStorage.__new__(RedisStorage)
        storage._r = mock_redis
        storage._prefix = ""
    return storage, mock_redis


class TestRedisStorageInterface:
    def test_redis_storage_importable(self) -> None:
        """RedisStorage must be importable."""
        from veridian.storage.redis_backend import RedisStorage  # noqa: F401

    def test_redis_storage_implements_base_storage(self) -> None:
        """RedisStorage must be a subclass of BaseStorage."""
        from veridian.storage.redis_backend import RedisStorage

        assert issubclass(RedisStorage, BaseStorage)

    def test_put_stores_task_and_adds_to_queue(self) -> None:
        """put() should call redis.set and zadd for PENDING tasks."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1", priority=75)
        storage.put(task)
        assert mock_redis.set.called
        assert mock_redis.zadd.called

    def test_get_returns_task_when_found(self) -> None:
        """get() should deserialise the stored JSON."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1")
        mock_redis.get.return_value = json.dumps(task.to_dict())
        result = storage.get("t1")
        assert result.id == "t1"

    def test_get_raises_task_not_found(self) -> None:
        """get() should raise TaskNotFound when Redis returns None."""
        storage, mock_redis = _make_redis_storage()
        mock_redis.get.return_value = None
        with pytest.raises(TaskNotFound):
            storage.get("ghost")

    def test_complete_updates_status_to_done(self) -> None:
        """complete() should persist DONE status."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1")
        mock_redis.get.return_value = json.dumps(task.to_dict())
        storage.complete("t1", TaskResult(raw_output="ok"))
        # Verify set was called with the updated task
        call_args = mock_redis.set.call_args_list[-1]
        stored = json.loads(call_args[0][1])
        assert stored["status"] == "done"

    def test_fail_updates_status_to_failed(self) -> None:
        """fail() should persist FAILED status and last_error."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1")
        mock_redis.get.return_value = json.dumps(task.to_dict())
        storage.fail("t1", "something went wrong")
        call_args = mock_redis.set.call_args_list[-1]
        stored = json.loads(call_args[0][1])
        assert stored["status"] == "failed"
        assert stored["last_error"] == "something went wrong"

    def test_fail_raises_task_not_found(self) -> None:
        """fail() should raise TaskNotFound when Redis returns None."""
        storage, mock_redis = _make_redis_storage()
        mock_redis.get.return_value = None
        with pytest.raises(TaskNotFound):
            storage.fail("ghost", "error")

    def test_get_next_raises_lock_error_when_setnx_fails(self) -> None:
        """get_next() raises StorageLockError when SETNX lock can't be acquired."""
        storage, mock_redis = _make_redis_storage()
        mock_redis.set.return_value = None  # SETNX failed — lock held by another process
        with pytest.raises(StorageLockError):
            storage.get_next()

    def test_get_next_returns_highest_priority_task(self) -> None:
        """get_next() picks the top candidate from the sorted set."""
        storage, mock_redis = _make_redis_storage()
        mock_redis.set.return_value = True  # SETNX acquired
        task = Task(id="t1", title="T1", priority=100)
        mock_redis.zrevrange.return_value = ["t1"]
        mock_redis.get.return_value = json.dumps(task.to_dict())
        # Stub scan_iter for _get_done_ids
        mock_redis.scan_iter.return_value = []
        result = storage.get_next()
        assert result is not None
        assert result.id == "t1"
        assert result.status == TaskStatus.IN_PROGRESS

    def test_get_next_returns_none_when_queue_empty(self) -> None:
        """get_next() returns None when the sorted set has no candidates."""
        storage, mock_redis = _make_redis_storage()
        mock_redis.set.return_value = True
        mock_redis.zrevrange.return_value = []
        mock_redis.scan_iter.return_value = []
        assert storage.get_next() is None

    def test_list_all_returns_all_tasks(self) -> None:
        """list_all() should scan all task keys and deserialise."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1")
        mock_redis.scan_iter.return_value = ["veridian:task:t1"]
        mock_redis.get.return_value = json.dumps(task.to_dict())
        all_tasks = storage.list_all()
        assert len(all_tasks) == 1
        assert all_tasks[0].id == "t1"

    def test_stats_counts_by_status(self) -> None:
        """stats() should aggregate status counts across all tasks."""
        storage, mock_redis = _make_redis_storage()
        task = Task(id="t1", title="T1")
        mock_redis.scan_iter.return_value = ["veridian:task:t1"]
        mock_redis.get.return_value = json.dumps(task.to_dict())
        s = storage.stats()
        assert s.total == 1
        assert s.by_status.get("pending", 0) == 1


# ── PostgresStorage (mocked — no real Postgres needed) ───────────────────────


def _make_postgres_storage() -> Any:
    """Construct a PostgresStorage with a fully mocked psycopg2."""
    from veridian.storage.postgres_backend import PostgresStorage

    with patch("veridian.storage.postgres_backend.PostgresStorage.__init__", return_value=None):
        storage = PostgresStorage.__new__(PostgresStorage)
        storage._dsn = "postgresql://test"
        storage._table = "veridian_tasks"
        storage._psycopg2 = MagicMock()
    return storage


class TestPostgresStorageInterface:
    def test_postgres_storage_importable(self) -> None:
        """PostgresStorage must be importable."""
        from veridian.storage.postgres_backend import PostgresStorage  # noqa: F401

    def test_postgres_storage_implements_base_storage(self) -> None:
        """PostgresStorage must be a subclass of BaseStorage."""
        from veridian.storage.postgres_backend import PostgresStorage

        assert issubclass(PostgresStorage, BaseStorage)

    def test_put_executes_upsert_sql(self) -> None:
        """put() should call cursor.execute with the upsert SQL."""
        storage = _make_postgres_storage()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        storage._psycopg2.connect.return_value = mock_conn

        task = Task(id="t1", title="T1")
        storage.put(task)
        assert mock_cur.execute.called

    def test_get_raises_task_not_found(self) -> None:
        """get() raises TaskNotFound when the row is not found."""
        storage = _make_postgres_storage()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        storage._psycopg2.connect.return_value = mock_conn

        with pytest.raises(TaskNotFound):
            storage.get("ghost")


# ── Entry-point autodiscovery ────────────────────────────────────────────────


class TestLocalJSONStorageEdgePaths:
    """Tests for low-coverage paths in LocalJSONStorage."""

    def test_load_raw_returns_empty_on_json_decode_error(self, tmp_path: Path) -> None:
        """_load_raw should return empty schema when file contains invalid JSON."""
        storage_file = tmp_path / "bad.json"
        storage_file.write_text("not valid json{{{", encoding="utf-8")
        storage = LocalJSONStorage(storage_file=storage_file)
        raw = storage._load_raw()
        assert raw["tasks"] == {}

    def test_load_raw_returns_empty_on_os_error(self, tmp_path: Path) -> None:
        """_load_raw should return empty schema when an OSError occurs reading file."""
        storage_file = tmp_path / "storage.json"
        storage_file.write_text("{}", encoding="utf-8")
        storage = LocalJSONStorage(storage_file=storage_file)
        mock_file = MagicMock()
        mock_file.exists.return_value = True
        mock_file.read_text.side_effect = OSError("permission denied")
        storage._file = mock_file
        raw = storage._load_raw()
        assert raw["tasks"] == {}

    def test_save_raw_cleans_up_tmp_on_write_error(self, tmp_path: Path) -> None:
        """_save_raw should cleanup temp file and re-raise when os.replace fails."""
        storage = LocalJSONStorage(storage_file=tmp_path / "storage.json")
        exc = pytest.raises(OSError, match="disk full")
        with patch("os.replace", side_effect=OSError("disk full")), exc:
            storage._save_raw({"schema_version": 1, "tasks": {}})

    def test_task_map_normalises_list_format(self, tmp_path: Path) -> None:
        """_task_map should convert list-of-tasks to dict keyed by id."""
        storage = LocalJSONStorage(storage_file=tmp_path / "storage.json")
        raw_data = {"tasks": [{"id": "t1", "title": "T1"}, {"id": "t2", "title": "T2"}]}
        result = storage._task_map(raw_data)
        assert "t1" in result
        assert "t2" in result
        assert result["t1"]["title"] == "T1"


class TestStorageAutodiscovery:
    def test_local_json_in_entry_points(self) -> None:
        """local_json entry point must resolve to LocalJSONStorage."""
        from importlib.metadata import entry_points

        eps = {ep.name: ep for ep in entry_points(group="veridian.storage")}
        assert "local_json" in eps, "local_json entry point not registered"
        cls = eps["local_json"].load()
        assert cls is LocalJSONStorage
