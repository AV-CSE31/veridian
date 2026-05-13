"""
tests.unit.test_phase1b_resource_lifecycle
──────────────────────────────────────────
Acceptance tests for Phase 1.B production-blocking lifecycle fixes:

* ``VeridianRunner.run`` saves and restores the parent SIGINT handler so
  nested runs don't leak handlers into the host process.
* ``ParallelRunner`` exposes async shutdown handlers that flip
  ``_shutdown`` on SIGINT/SIGTERM and drain between batches.
* ``RedisStorage.close()`` is idempotent and disconnects its connection
  pool cleanly.

The Postgres transaction-rollback fix is a pure plumbing change inside a
``try/except``; it's covered indirectly by the existing
``test_postgres_storage.py`` smoke tests and asserted here via a doc test.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any
from unittest.mock import MagicMock

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task
from veridian.ledger.ledger import TaskLedger
from veridian.loop.parallel_runner import ParallelRunner
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider


@pytest.fixture
def env(tmp_path):
    config = VeridianConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )
    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    provider = MockProvider()
    return config, provider, ledger


# ── SIGINT save/restore ──────────────────────────────────────────────────────


class TestSigintHandlerLifecycle:
    def test_runner_restores_previous_sigint_handler(self, env) -> None:
        config, provider, ledger = env

        sentinel_calls: list[object] = []

        def previous_handler(signum: int, frame: object) -> None:
            sentinel_calls.append((signum, frame))

        original = signal.signal(signal.SIGINT, previous_handler)
        try:
            runner = VeridianRunner(ledger=ledger, provider=provider, config=config)
            runner.run()  # no tasks → returns fast
            current = signal.getsignal(signal.SIGINT)
            assert current is previous_handler, (
                "VeridianRunner.run leaked its SIGINT handler into the parent"
            )
        finally:
            signal.signal(signal.SIGINT, original)

    def test_runner_restores_handler_on_exception(self, env) -> None:
        config, provider, ledger = env

        ledger.add([Task(title="will-explode", verifier_id="schema")])

        def previous_handler(signum: int, frame: object) -> None:
            pass

        original = signal.signal(signal.SIGINT, previous_handler)
        try:
            runner = VeridianRunner(ledger=ledger, provider=provider, config=config)

            # Force the task loop to raise so we exercise the finally path.
            runner._task_loop = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]
            with pytest.raises(RuntimeError, match="boom"):
                runner.run()

            assert signal.getsignal(signal.SIGINT) is previous_handler, (
                "Handler not restored on the exception path"
            )
        finally:
            signal.signal(signal.SIGINT, original)


# ── ParallelRunner SIGTERM drain ─────────────────────────────────────────────


class TestParallelRunnerShutdown:
    async def test_shutdown_flag_breaks_dispatch_loop(self, env) -> None:
        config, provider, ledger = env
        ledger.add([Task(title=f"t{i}", verifier_id="schema") for i in range(3)])

        runner = ParallelRunner(ledger=ledger, provider=provider, config=config)
        runner._shutdown = True  # pre-flip before any batch dispatches

        summary = await runner.run_async()
        # With shutdown set before the loop, no tasks are dispatched.
        assert summary.done_count == 0
        assert summary.failed_count == 0

    async def test_install_remove_signal_handlers(self, env) -> None:
        config, provider, ledger = env
        runner = ParallelRunner(ledger=ledger, provider=provider, config=config)
        loop = asyncio.get_running_loop()

        installed = runner._install_async_shutdown_handlers(loop)
        # On supported platforms we install at least one signal.
        try:
            # If anything was installed, removing them must be safe and idempotent.
            runner._uninstall_async_shutdown_handlers(loop, installed)
            runner._uninstall_async_shutdown_handlers(loop, installed)  # second time
        finally:
            # Best-effort final cleanup so the test loop is pristine.
            for sig in installed:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)

    def test_on_signal_sets_shutdown(self, env) -> None:
        config, provider, ledger = env
        runner = ParallelRunner(ledger=ledger, provider=provider, config=config)
        assert not runner._shutdown
        runner._on_async_shutdown_signal(signal.SIGTERM)
        assert runner._shutdown


# ── Redis pool lifecycle ─────────────────────────────────────────────────────


class TestRedisStorageClose:
    def test_close_is_idempotent_when_redis_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # We can't require a live Redis in CI. Stub the import + client so we
        # exercise the close() plumbing without a real connection.
        fake_pool = MagicMock()
        fake_client = MagicMock()

        class _FakeRedisModule:
            ConnectionPool = MagicMock(return_value=fake_pool)
            Redis = MagicMock(return_value=fake_client)

        import sys

        monkeypatch.setitem(sys.modules, "redis", _FakeRedisModule)

        from veridian.storage.redis_backend import RedisStorage

        storage = RedisStorage(host="localhost", port=6379)
        storage.close()
        storage.close()  # second call must be safe

        # Underlying client + pool both released exactly twice (idempotent
        # contextlib.suppress wrapping).
        assert fake_client.close.call_count == 2
        assert fake_pool.disconnect.call_count == 2


# ── Postgres rollback plumbing (smoke) ───────────────────────────────────────


class TestPostgresRollbackInvariant:
    def test_get_next_rolls_back_on_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify rollback is issued when the dependency gate filters all rows.

        We patch _connect to return a mock connection so we observe the
        rollback call without needing a real database.
        """
        from veridian.storage.postgres_backend import PostgresStorage

        # Construct without touching the real DB.
        storage = PostgresStorage.__new__(PostgresStorage)
        storage._table = "tasks"  # type: ignore[attr-defined]

        fake_cursor = MagicMock()
        fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
        fake_cursor.__exit__ = MagicMock(return_value=False)
        fake_cursor.fetchall.return_value = []

        fake_conn = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value = fake_cursor

        def fake_connect(self_: Any) -> MagicMock:
            return fake_conn

        monkeypatch.setattr(PostgresStorage, "_connect", fake_connect)

        result = storage.get_next()
        assert result is None
        assert fake_conn.rollback.called, "rollback must be issued on empty queue"
