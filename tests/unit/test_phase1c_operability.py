"""Acceptance tests for container path and ledger-lock operability."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from veridian.core.config import VeridianConfig, default_data_dir
from veridian.ledger.ledger import TaskLedger


class TestDefaultDataDir:
    def test_env_var_creates_and_returns_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "varlib" / "veridian"
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(target)}, clear=False):
            resolved = default_data_dir()
        assert resolved == target
        assert resolved.exists() and resolved.is_dir()

    def test_no_env_var_falls_back_to_cwd(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        env.pop("VERIDIAN_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            cwd_before = Path.cwd()
            assert default_data_dir() == cwd_before

    def test_config_defaults_anchor_paths_to_data_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "anchored"
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(target)}, clear=False):
            cfg = VeridianConfig()
        assert cfg.ledger_file.parent == target
        assert cfg.progress_file.parent == target


class TestLedgerLockTimeoutKnob:
    def test_config_exposes_default(self) -> None:
        cfg = VeridianConfig()
        assert cfg.ledger_lock_timeout == 15.0

    def test_ledger_receives_configured_timeout(self, tmp_path: Path) -> None:
        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
            ledger_lock_timeout=2.5,
        )
        ledger = TaskLedger(
            path=config.ledger_file,
            progress_file=str(config.progress_file),
            lock_timeout=config.ledger_lock_timeout,
        )
        assert ledger._lock.timeout == pytest.approx(2.5)
