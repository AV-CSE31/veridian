"""
tests.unit.test_phase6b_durability
------------------------------------------------------------------------------------------------------
Acceptance tests for Phase 6.B bug-hunt fixes:

* fsync runs as part of ``atomic_write_text`` so the kernel commits
  bytes to disk before the rename.
* ``VERIDIAN_ATOMIC_IO_SKIP_FSYNC=1`` lets test suites skip the fsync
  without changing the rest of the write path.
* ``ContextManager`` rejects ``context_files`` entries outside the
  configured data dir unless the operator opts in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.atomic_io import atomic_write_text

# ------ fsync ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


class TestAtomicWriteFsync:
    def test_fsync_called_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import veridian.core.atomic_io as mod

        called: list[int] = []

        def _record_fsync(fd: int) -> None:
            called.append(fd)

        # Make sure no opt-out is set so the helper actually calls fsync.
        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", _record_fsync)
        atomic_write_text(tmp_path / "out.txt", "x")
        assert len(called) == 1

    def test_env_var_disables_fsync(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import veridian.core.atomic_io as mod

        called: list[int] = []

        def _record_fsync(fd: int) -> None:
            called.append(fd)

        monkeypatch.setenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", "1")
        monkeypatch.setattr(mod.os, "fsync", _record_fsync)
        atomic_write_text(tmp_path / "out.txt", "x")
        assert called == []

    def test_fsync_oserror_swallowed_but_write_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Some filesystems / network mounts raise OSError on fsync. The
        # helper must surface a written file even when fsync fails, and
        # the durability downgrade must be visible in the logs.
        import logging

        import veridian.core.atomic_io as mod

        def _boom(_fd: int) -> None:
            raise OSError("fsync unsupported")

        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", _boom)
        target = tmp_path / "ok.txt"
        with caplog.at_level(logging.WARNING, logger="veridian.core.atomic_io"):
            atomic_write_text(target, "payload")
        assert target.read_text(encoding="utf-8") == "payload"
        assert any("fsync_failed" in rec.message for rec in caplog.records)


# ------ TaskLedger fsync ------------------------------------------------------------------------------------------------------------------------------------


class TestLedgerWriteFsync:
    def _build_ledger(self, tmp_path: Path):
        from veridian.ledger.ledger import TaskLedger

        return TaskLedger(
            path=tmp_path / "ledger.json", progress_file=str(tmp_path / "progress.md")
        )

    def _make_task(self):
        from veridian.core.task import Task

        return Task(title="durable", description="fsync before rename")

    def test_fsync_called_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import veridian.ledger.ledger as mod

        ledger = self._build_ledger(tmp_path)

        called: list[int] = []
        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", lambda fd: called.append(fd))
        ledger.add([self._make_task()])
        assert len(called) >= 1

    def test_env_var_disables_fsync(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import veridian.ledger.ledger as mod

        ledger = self._build_ledger(tmp_path)

        called: list[int] = []
        monkeypatch.setenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", "1")
        monkeypatch.setattr(mod.os, "fsync", lambda fd: called.append(fd))
        ledger.add([self._make_task()])
        assert called == []

    def test_fsync_oserror_prevents_successful_acknowledgement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import veridian.ledger.wal as mod

        ledger = self._build_ledger(tmp_path)
        task = self._make_task()
        real_fsync = mod.os.fsync

        def _boom(_fd: int) -> None:
            raise OSError("fsync unsupported")

        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", _boom)
        with pytest.raises(OSError, match="fsync unsupported"):
            ledger.add([task])

        # Restore the durability primitive before observing recovery. The WAL
        # bytes may have reached the file, but without an advanced durable head
        # they were never acknowledged and must not become visible.
        monkeypatch.setattr(mod.os, "fsync", real_fsync)
        assert ledger.list() == []
        reopened = self._build_ledger(tmp_path)
        assert reopened.list() == []


# ------ Bootstrap write race ------------------------------------------------------------------------------------------------------------------------------


class TestBootstrapRace:
    def test_losing_init_race_does_not_clobber_populated_ledger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Two processes init the same ledger path concurrently. The loser must
        not replace the winner's populated ledger with an empty bootstrap.
        Simulated deterministically: a stub lock lets "the other process"
        create and populate the ledger while we wait to acquire.
        """
        import veridian.ledger.ledger as mod
        from veridian.core.task import Task
        from veridian.ledger.ledger import TaskLedger

        ledger_path = tmp_path / "ledger.json"

        class RaceyLock:
            """Winner populates the ledger during our acquire()."""

            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> RaceyLock:
                if not ledger_path.exists():
                    winner = object.__new__(TaskLedger)
                    winner.path = ledger_path
                    winner.run_id = "winner"
                    winner.progress_path = tmp_path / "winner-progress.md"
                    winner._lock_path = ledger_path.with_suffix(".lock")
                    winner._lock = self
                    # ``_write_raw`` persists the fail-closed WAL identity in
                    # every snapshot.  This deliberately partial test double
                    # must therefore model the constructor state established
                    # before a real TaskLedger attempts to acquire its lock.
                    winner._ledger_id = "winner-ledger"
                    winner._generation = 1
                    winner._write_raw(
                        {
                            "schema_version": mod.SCHEMA_VERSION,
                            "tasks": {
                                "keep-me": Task(
                                    id="keep-me", title="winner task", description="d"
                                ).to_dict()
                            },
                        }
                    )
                return self

            def __exit__(self, *exc: object) -> None:
                pass

        monkeypatch.setattr(mod, "FileLock", RaceyLock)
        loser = TaskLedger(path=ledger_path, progress_file=str(tmp_path / "loser-progress.md"))

        # The winner's task must survive the loser's __init__.
        assert loser.get("keep-me").title == "winner task"


# ------ Orphan temp-file sweep ------------------------------------------------------------------------------------------------------------------------------


class TestOrphanTmpSweep:
    def _build_ledger(self, tmp_path: Path):
        from veridian.ledger.ledger import TaskLedger

        return TaskLedger(
            path=tmp_path / "ledger.json", progress_file=str(tmp_path / "progress.md")
        )

    def test_stale_legacy_named_tmp_is_preserved_on_reset(self, tmp_path: Path) -> None:
        import os
        import time

        ledger = self._build_ledger(tmp_path)
        stale = tmp_path / "ledger_dead1234.tmp"
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - 120
        os.utime(stale, (old, old))

        ledger.reset_in_progress()
        # Cleanup is deliberately restricted to this ledger's atomic snapshot,
        # WAL, and WAL-head temp names. A generic legacy-looking temp may belong
        # to another producer and must not be deleted based on age alone.
        assert stale.exists()

    def test_fresh_tmp_files_are_preserved(self, tmp_path: Path) -> None:
        # A young temp file may belong to a sibling ledger mid-write in a
        # shared directory; the sweep must not race its rename.
        ledger = self._build_ledger(tmp_path)
        fresh = tmp_path / "ledger_live5678.tmp"
        fresh.write_text("{}", encoding="utf-8")

        ledger.reset_in_progress()
        assert fresh.exists()

    def test_unrelated_files_untouched(self, tmp_path: Path) -> None:
        import os
        import time

        ledger = self._build_ledger(tmp_path)
        other = tmp_path / "notes.tmp"
        other.write_text("keep me", encoding="utf-8")
        old = time.time() - 120
        os.utime(other, (old, old))

        ledger.reset_in_progress()
        assert other.exists()


# ------ ContextManager path-traversal guard ---------------------------------------------------------------------------------------------------------------


class TestContextFilesPathGuard:
    def _build_manager(self, tmp_path: Path):
        from veridian.context.manager import ContextManager
        from veridian.context.window import TokenWindow
        from veridian.providers.mock_provider import MockProvider

        return ContextManager(
            window=TokenWindow(capacity=8000),
            provider=MockProvider(),
            progress_path=tmp_path / "progress.md",
        )

    def test_inside_data_dir_allowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERIDIAN_DATA_DIR", str(tmp_path))
        ok = tmp_path / "ok.txt"
        ok.write_text("inside", encoding="utf-8")

        manager = self._build_manager(tmp_path)
        out = manager._build_environment_block([str(ok)])
        assert "inside" in out

    def test_outside_data_dir_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import logging

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("VERIDIAN_DATA_DIR", str(data_dir))
        # Drop the explicit-opt-in so the guard fires.
        monkeypatch.delenv("VERIDIAN_CONTEXT_ALLOW_OUTSIDE_DATA_DIR", raising=False)

        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        manager = self._build_manager(data_dir)
        with caplog.at_level(logging.WARNING, logger="veridian.context.manager"):
            out = manager._build_environment_block([str(outside)])

        # The file's contents must not leak into the prompt.
        assert "secret" not in out
        assert any("outside_data_dir" in rec.message for rec in caplog.records)

    def test_outside_opt_in_lets_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("VERIDIAN_DATA_DIR", str(data_dir))
        monkeypatch.setenv("VERIDIAN_CONTEXT_ALLOW_OUTSIDE_DATA_DIR", "1")

        outside = tmp_path / "outside.txt"
        outside.write_text("explicit-opt-in", encoding="utf-8")

        manager = self._build_manager(data_dir)
        out = manager._build_environment_block([str(outside)])
        assert "explicit-opt-in" in out
