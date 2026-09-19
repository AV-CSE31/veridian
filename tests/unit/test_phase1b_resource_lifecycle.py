"""
tests.unit.test_phase1b_resource_lifecycle

Acceptance tests for production-blocking lifecycle fixes:

* ``ChitRunner.run`` saves and restores the parent SIGINT handler so
  nested runs do not leak handlers into the host process.
"""

from __future__ import annotations

import signal
from unittest.mock import MagicMock

import pytest

from chit.core.config import ChitConfig
from chit.core.task import Task
from chit.ledger.ledger import TaskLedger
from chit.loop.runner import ChitRunner
from chit.providers.mock_provider import MockProvider


@pytest.fixture
def env(tmp_path):
    config = ChitConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=tmp_path / "progress.md",
    )
    ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
    provider = MockProvider()
    return config, provider, ledger


class TestSigintHandlerLifecycle:
    def test_runner_restores_previous_sigint_handler(self, env) -> None:
        config, provider, ledger = env

        sentinel_calls: list[object] = []

        def previous_handler(signum: int, frame: object) -> None:
            sentinel_calls.append((signum, frame))

        original = signal.signal(signal.SIGINT, previous_handler)
        try:
            runner = ChitRunner(ledger=ledger, provider=provider, config=config)
            runner.run()  # no tasks, returns fast
            current = signal.getsignal(signal.SIGINT)
            assert current is previous_handler, (
                "ChitRunner.run leaked its SIGINT handler into the parent"
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
            runner = ChitRunner(ledger=ledger, provider=provider, config=config)

            runner._task_loop = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]
            with pytest.raises(RuntimeError, match="boom"):
                runner.run()

            assert signal.getsignal(signal.SIGINT) is previous_handler, (
                "Handler not restored on the exception path"
            )
        finally:
            signal.signal(signal.SIGINT, original)
