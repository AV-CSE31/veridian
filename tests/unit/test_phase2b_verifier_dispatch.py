"""
tests.unit.test_phase2b_verifier_dispatch
---------------------------------------------------------------------------------------------------------------------------
Acceptance tests for the Phase 2.B verifier-dispatch optimisations:

* ``VerifierRegistry.get`` returns a shared instance when the class opts
  in via ``shareable=True`` and the config matches.
* Re-registering a class invalidates any cached instance.
* The default (``shareable=False``) behaviour is unchanged: a fresh
  instance is constructed per call.
* ``VeridianRunner.__init__`` wires the built-in registry without importing
  verifier modules; each built-in loads lazily by ID.
"""

from __future__ import annotations

from typing import Any, ClassVar

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult, VerifierRegistry


class _CountingShareable(BaseVerifier):
    id: ClassVar[str] = "test_share"
    shareable: ClassVar[bool] = True
    construct_count: ClassVar[int] = 0

    def __init__(self, **config: Any) -> None:
        type(self).construct_count += 1
        self.config = config

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class _CountingFresh(BaseVerifier):
    id: ClassVar[str] = "test_fresh"
    shareable: ClassVar[bool] = False
    construct_count: ClassVar[int] = 0

    def __init__(self, **config: Any) -> None:
        type(self).construct_count += 1
        self.config = config

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class TestShareableInstanceCache:
    def test_same_config_returns_same_instance(self) -> None:
        reg = VerifierRegistry()
        reg.register(_CountingShareable)
        _CountingShareable.construct_count = 0

        a = reg.get("test_share", {"alpha": 1})
        b = reg.get("test_share", {"alpha": 1})

        assert a is b
        assert _CountingShareable.construct_count == 1

    def test_config_order_canonicalised(self) -> None:
        reg = VerifierRegistry()
        reg.register(_CountingShareable)
        _CountingShareable.construct_count = 0

        a = reg.get("test_share", {"alpha": 1, "beta": 2})
        b = reg.get("test_share", {"beta": 2, "alpha": 1})

        assert a is b
        assert _CountingShareable.construct_count == 1

    def test_different_config_returns_different_instance(self) -> None:
        reg = VerifierRegistry()
        reg.register(_CountingShareable)
        _CountingShareable.construct_count = 0

        a = reg.get("test_share", {"alpha": 1})
        b = reg.get("test_share", {"alpha": 2})

        assert a is not b
        assert _CountingShareable.construct_count == 2

    def test_re_registration_invalidates_cache(self) -> None:
        reg = VerifierRegistry()
        reg.register(_CountingShareable)
        _CountingShareable.construct_count = 0

        first = reg.get("test_share", {"alpha": 1})

        reg.register(_CountingShareable)  # re-register same class
        second = reg.get("test_share", {"alpha": 1})

        assert first is not second
        assert _CountingShareable.construct_count == 2

    def test_shareable_false_always_constructs(self) -> None:
        reg = VerifierRegistry()
        reg.register(_CountingFresh)
        _CountingFresh.construct_count = 0

        reg.get("test_fresh", {"alpha": 1})
        reg.get("test_fresh", {"alpha": 1})
        reg.get("test_fresh", {"alpha": 1})

        assert _CountingFresh.construct_count == 3


class TestLazyRegistryInit:
    def test_runner_resolves_lazy_registry_in_init(self, tmp_path) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import VeridianRunner
        from veridian.providers.mock_provider import MockProvider

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        runner = VeridianRunner(ledger=ledger, provider=MockProvider(), config=config)

        assert runner._verifier_registry is not None
        assert "schema" in runner._verifier_registry._lazy
