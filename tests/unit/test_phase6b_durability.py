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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Some filesystems / network mounts raise OSError on fsync. The
        # helper must surface a written file even when fsync fails.
        import veridian.core.atomic_io as mod

        def _boom(_fd: int) -> None:
            raise OSError("fsync unsupported")

        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", _boom)
        target = tmp_path / "ok.txt"
        atomic_write_text(target, "payload")
        assert target.read_text(encoding="utf-8") == "payload"


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

    def test_fsync_oserror_swallowed_but_write_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import veridian.ledger.ledger as mod

        ledger = self._build_ledger(tmp_path)

        def _boom(_fd: int) -> None:
            raise OSError("fsync unsupported")

        monkeypatch.delenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", raising=False)
        monkeypatch.setattr(mod.os, "fsync", _boom)
        ledger.add([self._make_task()])
        assert (tmp_path / "ledger.json").exists()
        assert len(ledger.list()) == 1


# ------ Orphan temp-file sweep ------------------------------------------------------------------------------------------------------------------------------


class TestOrphanTmpSweep:
    def _build_ledger(self, tmp_path: Path):
        from veridian.ledger.ledger import TaskLedger

        return TaskLedger(
            path=tmp_path / "ledger.json", progress_file=str(tmp_path / "progress.md")
        )

    def test_stale_tmp_files_removed_on_reset(self, tmp_path: Path) -> None:
        import os
        import time

        ledger = self._build_ledger(tmp_path)
        stale = tmp_path / "ledger_dead1234.tmp"
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - 120
        os.utime(stale, (old, old))

        ledger.reset_in_progress()
        assert not stale.exists()

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
