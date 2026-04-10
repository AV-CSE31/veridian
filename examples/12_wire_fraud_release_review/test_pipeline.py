"""
Tests for Problem 12: Wire Fraud Release Review.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from veridian.core.config import VeridianConfig
from veridian.core.task import TaskStatus
from veridian.hooks.registry import HookRegistry
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.mock_provider import MockProvider


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


for stale in ("pipeline",):
    sys.modules.pop(stale, None)

_pipeline = _load_local_module("pipeline.py", f"{Path(__file__).parent.name}_pipeline")

DualApprovalHook = _pipeline.DualApprovalHook
WireGateway = _pipeline.WireGateway
build_task = _pipeline.build_task
grant_dual_approval = _pipeline.grant_dual_approval
load_payment_cases = _pipeline.load_payment_cases
release_approved_wires = _pipeline.release_approved_wires
script_worker_outputs = _pipeline.script_worker_outputs


def _config(tmp_path: Path) -> VeridianConfig:
    return VeridianConfig(
        max_turns_per_task=1,
        max_retries=1,
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
        activity_journal_enabled=True,
        resume_paused_on_start=True,
    )


def test_suspicious_payment_pauses_until_dual_approval(tmp_path: Path) -> None:
    config = _config(tmp_path)
    cases = [c for c in load_payment_cases() if c["id"] == "wire-task-002"]
    task = build_task(cases[0])

    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    ledger.add([task])

    provider = MockProvider()
    script_worker_outputs(provider, cases)

    hooks = HookRegistry()
    hooks.register(DualApprovalHook(required_approvals=2))
    runner = VeridianRunner(ledger=ledger, provider=provider, config=config, hooks=hooks)

    first = runner.run()
    paused = ledger.get(task.id)
    assert first.done_count == 0
    assert paused.status == TaskStatus.PAUSED
    assert paused.result is not None
    assert (
        paused.result.extras["pause_payload"]["reason"] == "Dual approval required before release"
    )

    grant_dual_approval(ledger, task.id, ["ops_manager", "risk_officer"])
    second = runner.run()
    done = ledger.get(task.id)
    assert second.done_count == 1
    assert done.status == TaskStatus.DONE
    assert done.result is not None
    assert done.result.structured["decision"] == "ALLOW"


def test_sanctioned_payment_is_blocked_and_never_released(tmp_path: Path) -> None:
    config = _config(tmp_path)
    cases = [c for c in load_payment_cases() if c["id"] == "wire-task-003"]
    task = build_task(cases[0])

    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    ledger.add([task])

    provider = MockProvider()
    script_worker_outputs(provider, cases)

    runner = VeridianRunner(
        ledger=ledger,
        provider=provider,
        config=config,
        hooks=HookRegistry(),
    )
    summary = runner.run()
    assert summary.done_count == 1

    gateway = WireGateway()
    statuses = release_approved_wires(ledger, gateway)
    stored = ledger.get(task.id)
    assert gateway.calls == 0
    assert statuses[task.id] == "blocked"
    assert stored.result is not None
    assert stored.result.extras["release_status"] == "blocked"


def test_release_activity_is_idempotent_across_replay(tmp_path: Path) -> None:
    config = _config(tmp_path)
    cases = [c for c in load_payment_cases() if c["id"] == "wire-task-001"]
    task = build_task(cases[0])

    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    ledger.add([task])

    provider = MockProvider()
    script_worker_outputs(provider, cases)
    runner = VeridianRunner(
        ledger=ledger,
        provider=provider,
        config=config,
        hooks=HookRegistry(),
    )
    summary = runner.run()
    assert summary.done_count == 1

    gateway = WireGateway()
    first_status = release_approved_wires(ledger, gateway)
    assert first_status[task.id] == "released"
    assert gateway.calls == 1

    replay_ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    second_status = release_approved_wires(replay_ledger, gateway)
    replayed = replay_ledger.get(task.id)
    assert second_status[task.id] == "released"
    assert gateway.calls == 1
    assert replayed.result is not None
    journal = replayed.result.extras["release_activity_journal"]
    assert isinstance(journal, list)
    assert len(journal) == 1
