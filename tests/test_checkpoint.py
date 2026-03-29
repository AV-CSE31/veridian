"""
tests.test_checkpoint
─────────────────────
Tests for F3.4 — Checkpoint/Restore for Long-Running Tasks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from veridian.core.checkpoint import (
    Checkpoint,
    CheckpointManager,
    CheckpointConfig,
)


# ─── Checkpoint model tests ───────────────────────────────────────────────────


class TestCheckpoint:
    def test_creation(self) -> None:
        cp = Checkpoint(
            checkpoint_id="cp_001",
            task_state={"step": 3, "results": ["a", "b"]},
        )
        assert cp.checkpoint_id == "cp_001"
        assert cp.task_state["step"] == 3

    def test_to_dict_roundtrip(self) -> None:
        cp = Checkpoint(
            checkpoint_id="cp_001",
            task_state={"step": 3},
            verification_history=[{"verifier": "schema", "passed": True}],
            agent_context={"model": "claude-3"},
        )
        d = cp.to_dict()
        cp2 = Checkpoint.from_dict(d)
        assert cp2.checkpoint_id == cp.checkpoint_id
        assert cp2.task_state == cp.task_state
        assert cp2.verification_history == cp.verification_history
        assert cp2.agent_context == cp.agent_context

    def test_timestamp_set_on_creation(self) -> None:
        cp = Checkpoint(checkpoint_id="cp_001", task_state={})
        assert cp.created_at is not None


# ─── CheckpointConfig tests ───────────────────────────────────────────────────


class TestCheckpointConfig:
    def test_defaults(self) -> None:
        cfg = CheckpointConfig()
        assert cfg.interval_steps > 0
        assert cfg.max_checkpoints > 0

    def test_custom_interval(self) -> None:
        cfg = CheckpointConfig(interval_steps=5, max_checkpoints=3)
        assert cfg.interval_steps == 5
        assert cfg.max_checkpoints == 3


# ─── CheckpointManager tests ──────────────────────────────────────────────────


class TestCheckpointManager:
    def test_save_and_restore(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        cp = mgr.save(task_state={"step": 1, "data": "hello"})
        assert cp.checkpoint_id is not None

        restored = mgr.restore_latest()
        assert restored is not None
        assert restored.task_state["data"] == "hello"

    def test_restore_returns_none_when_empty(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(storage_dir=tmp_path, run_id="run_001")
        assert mgr.restore_latest() is None

    def test_multiple_saves_restore_last(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1, max_checkpoints=10)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        mgr.save(task_state={"step": 1})
        mgr.save(task_state={"step": 2})
        mgr.save(task_state={"step": 3})

        restored = mgr.restore_latest()
        assert restored is not None
        assert restored.task_state["step"] == 3

    def test_max_checkpoints_evicts_oldest(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1, max_checkpoints=2)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        mgr.save(task_state={"step": 1})
        mgr.save(task_state={"step": 2})
        mgr.save(task_state={"step": 3})

        checkpoints = mgr.list_checkpoints()
        assert len(checkpoints) <= 2

    def test_interval_steps_skips_save(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=3)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        # step 1: no save (not at interval)
        cp1 = mgr.maybe_save(step=1, task_state={"step": 1})
        assert cp1 is None

        # step 3: save (at interval)
        cp3 = mgr.maybe_save(step=3, task_state={"step": 3})
        assert cp3 is not None

        # step 5: no save
        cp5 = mgr.maybe_save(step=5, task_state={"step": 5})
        assert cp5 is None

        # step 6: save
        cp6 = mgr.maybe_save(step=6, task_state={"step": 6})
        assert cp6 is not None

    def test_checkpoint_file_is_written_atomically(self, tmp_path: Path) -> None:
        """No .tmp files should remain after save."""
        cfg = CheckpointConfig(interval_steps=1)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")
        mgr.save(task_state={"step": 1})

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_includes_verification_history(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        history = [{"verifier": "schema", "passed": True, "at": "2024-01-01T00:00:00"}]
        cp = mgr.save(task_state={"step": 1}, verification_history=history)

        restored = mgr.restore_latest()
        assert restored is not None
        assert len(restored.verification_history) == 1

    def test_save_includes_agent_context(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        context = {"model": "claude-3", "tokens_used": 1000}
        cp = mgr.save(task_state={}, agent_context=context)

        restored = mgr.restore_latest()
        assert restored is not None
        assert restored.agent_context["model"] == "claude-3"

    def test_list_checkpoints_sorted_newest_first(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1, max_checkpoints=10)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        for i in range(3):
            mgr.save(task_state={"step": i})

        checkpoints = mgr.list_checkpoints()
        steps = [cp.task_state["step"] for cp in checkpoints]
        assert steps == sorted(steps, reverse=True)

    def test_clear_removes_all_checkpoints(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1, max_checkpoints=10)
        mgr = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_001")

        for i in range(3):
            mgr.save(task_state={"step": i})

        mgr.clear()
        assert mgr.restore_latest() is None
        assert len(mgr.list_checkpoints()) == 0

    def test_different_run_ids_isolated(self, tmp_path: Path) -> None:
        cfg = CheckpointConfig(interval_steps=1, max_checkpoints=10)
        mgr_a = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_a")
        mgr_b = CheckpointManager(storage_dir=tmp_path, config=cfg, run_id="run_b")

        mgr_a.save(task_state={"run": "a"})
        assert mgr_b.restore_latest() is None
