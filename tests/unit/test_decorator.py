"""
tests.unit.test_decorator
──────────────────────────
Tests for the @verified decorator (veridian/decorator.py).

Coverage:
- Zero-config usage (@verified with no args)
- Explicit verifier list
- Sync and async functions
- on_fail="raise" raises VerificationError
- on_fail="log" logs but does NOT raise
- on_fail="retry" retries up to max_retries
- TaskLedger entries are created and tracked
- Provenance tokens are generated
- Custom metadata is stored
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

import veridian.decorator as decorator_module
from veridian.core.exceptions import VerificationError
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.decorator import (
    NotEmptyVerifier,
    NotNoneVerifier,
    TypeCheckVerifier,
    _generate_provenance,
    _make_task_result,
    _resolve_verifiers,
    verified,
)
from veridian.ledger.ledger import TaskLedger
from veridian.verify.base import BaseVerifier, VerificationResult

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path: Path) -> TaskLedger:
    return TaskLedger(
        path=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


class AlwaysPassVerifier(BaseVerifier):
    id = "always_pass"
    description = "Always passes."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True, evidence={"test": "pass"})


class AlwaysFailVerifier(BaseVerifier):
    id = "always_fail"
    description = "Always fails."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="deliberate failure")


# ══════════════════════════════════════════════════════════════════════════════
# Zero-config usage
# ══════════════════════════════════════════════════════════════════════════════


class TestZeroConfig:
    def test_bare_decorator_sync(self):
        """@verified (no parens) works on a sync function."""

        @verified
        def greet(name: str) -> str:
            return f"hello {name}"

        result = greet("world")
        assert result == "hello world"

    def test_bare_decorator_async(self):
        """@verified (no parens) works on an async function."""

        @verified
        async def greet(name: str) -> str:
            return f"hello {name}"

        result = asyncio.run(greet("world"))
        assert result == "hello world"

    def test_preserves_function_name(self):
        """@verified preserves __name__ and __doc__."""

        @verified
        def my_func() -> str:
            """My docstring."""
            return "ok"

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "My docstring."

    def test_preserves_return_value_sync(self):
        """Return value passes through unchanged."""

        @verified
        def compute() -> int:
            return 42

        assert compute() == 42

    def test_preserves_return_value_dict(self):
        """Dict return value passes through unchanged."""

        @verified
        def report() -> dict:
            return {"status": "ok", "count": 3}

        assert report() == {"status": "ok", "count": 3}


# ══════════════════════════════════════════════════════════════════════════════
# Explicit verifiers
# ══════════════════════════════════════════════════════════════════════════════


class TestExplicitVerifiers:
    def test_verifier_instance_list(self):
        """Accepts list of BaseVerifier instances."""

        @verified(verifiers=[AlwaysPassVerifier()])
        def fn() -> str:
            return "ok"

        assert fn() == "ok"

    def test_verifier_string_ids(self):
        """Accepts list of registered verifier ID strings."""

        @verified(verifiers=["not_none", "not_empty"])
        def fn() -> str:
            return "hello"

        assert fn() == "hello"

    def test_mixed_verifiers(self):
        """Accepts a mix of strings and instances."""

        @verified(verifiers=["not_none", AlwaysPassVerifier()])
        def fn() -> str:
            return "ok"

        assert fn() == "ok"

    def test_unknown_verifier_id_skipped_with_warning(self, caplog):
        """Unknown verifier ID is skipped with a warning, not a crash."""

        with caplog.at_level(logging.WARNING, logger="veridian.decorator"):

            @verified(verifiers=["nonexistent_verifier_xyz"])
            def fn() -> str:
                return "ok"

            result = fn()

        assert result == "ok"
        assert "nonexistent_verifier_xyz" in caplog.text


# ══════════════════════════════════════════════════════════════════════════════
# Sync vs async
# ══════════════════════════════════════════════════════════════════════════════


class TestSyncAsync:
    def test_sync_function_executes(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        def add(a: int, b: int) -> int:
            return a + b

        assert add(1, 2) == 3

    def test_async_function_executes(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        async def add(a: int, b: int) -> int:
            return a + b

        result = asyncio.run(add(1, 2))
        assert result == 3

    def test_async_with_no_args(self):
        @verified
        async def async_noop() -> str:
            return "done"

        result = asyncio.run(async_noop())
        assert result == "done"

    def test_sync_passes_args_and_kwargs(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        def concat(a: str, b: str, sep: str = "-") -> str:
            return f"{a}{sep}{b}"

        assert concat("foo", "bar", sep="|") == "foo|bar"

    def test_async_passes_args_and_kwargs(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        async def concat(a: str, b: str, sep: str = "-") -> str:
            return f"{a}{sep}{b}"

        result = asyncio.run(concat("foo", "bar", sep="|"))
        assert result == "foo|bar"


# ══════════════════════════════════════════════════════════════════════════════
# on_fail="raise"
# ══════════════════════════════════════════════════════════════════════════════


class TestOnFailRaise:
    def test_raises_verification_error_on_failure(self):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="raise")
        def fn() -> str:
            return "output"

        with pytest.raises(VerificationError) as exc_info:
            fn()

        assert "fn" in str(exc_info.value)
        assert "deliberate failure" in str(exc_info.value)

    def test_raise_is_default_on_fail(self):
        """on_fail='raise' is the default."""

        @verified(verifiers=[AlwaysFailVerifier()])
        def fn() -> str:
            return "output"

        with pytest.raises(VerificationError):
            fn()

    def test_raises_on_none_return_by_default(self):
        """Default verifiers catch None returns."""

        @verified
        def fn() -> str:
            return None  # type: ignore[return-value]

        with pytest.raises(VerificationError):
            fn()

    def test_does_not_raise_on_pass(self):
        @verified(verifiers=[AlwaysPassVerifier()], on_fail="raise")
        def fn() -> str:
            return "ok"

        assert fn() == "ok"

    def test_async_raises_on_failure(self):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="raise")
        async def fn() -> str:
            return "output"

        with pytest.raises(VerificationError):
            asyncio.run(fn())


# ══════════════════════════════════════════════════════════════════════════════
# on_fail="log"
# ══════════════════════════════════════════════════════════════════════════════


class TestOnFailLog:
    def test_does_not_raise_on_failure(self):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="log")
        def fn() -> str:
            return "output"

        result = fn()
        assert result == "output"

    def test_logs_warning_on_failure(self, caplog):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="log")
        def fn() -> str:
            return "output"

        with caplog.at_level(logging.WARNING, logger="veridian.decorator"):
            fn()

        assert "fn" in caplog.text
        assert "deliberate failure" in caplog.text

    def test_async_does_not_raise_on_failure(self):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="log")
        async def fn() -> str:
            return "output"

        result = asyncio.run(fn())
        assert result == "output"

    def test_returns_value_even_on_failure(self):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="log")
        def fn() -> int:
            return 99

        assert fn() == 99


# ══════════════════════════════════════════════════════════════════════════════
# on_fail="retry"
# ══════════════════════════════════════════════════════════════════════════════


class TestOnFailRetry:
    def test_retries_and_succeeds(self):
        """Function fails once, then succeeds — should return correct value."""
        call_count = [0]

        call_counts_for_verify = [0]

        class OnceFailVerifier(BaseVerifier):
            id = "once_fail"
            description = "Fails on first call, passes afterwards."

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                call_counts_for_verify[0] += 1
                if call_counts_for_verify[0] == 1:
                    return VerificationResult(passed=False, error="first call fails")
                return VerificationResult(passed=True)

        @verified(verifiers=[OnceFailVerifier()], on_fail="retry", max_retries=3)
        def fn() -> str:
            call_count[0] += 1
            return f"call-{call_count[0]}"

        result = fn()
        assert call_count[0] == 2
        assert result == "call-2"

    def test_raises_after_max_retries_exhausted(self):
        """Always-failing verifier raises after max_retries attempts."""
        call_count = [0]

        @verified(verifiers=[AlwaysFailVerifier()], on_fail="retry", max_retries=3)
        def fn() -> str:
            call_count[0] += 1
            return "output"

        with pytest.raises(VerificationError) as exc_info:
            fn()

        assert call_count[0] == 3
        assert "3" in str(exc_info.value)

    def test_async_retries_and_succeeds(self):
        """Async: retries on failure, returns value on success."""
        call_count = [0]

        class OnceFail2(BaseVerifier):
            id = "once_fail_2"
            description = "Fails on first call."

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                call_count[0] += 1
                if call_count[0] == 1:
                    return VerificationResult(passed=False, error="transient failure")
                return VerificationResult(passed=True)

        @verified(verifiers=[OnceFail2()], on_fail="retry", max_retries=3)
        async def fn() -> str:
            return "value"

        result = asyncio.run(fn())
        assert result == "value"

    def test_max_retries_default_is_1(self):
        """Default max_retries=1 means exactly 1 attempt with retry policy."""
        call_count = [0]

        @verified(verifiers=[AlwaysFailVerifier()], on_fail="retry", max_retries=1)
        def fn() -> str:
            call_count[0] += 1
            return "output"

        with pytest.raises(VerificationError):
            fn()

        assert call_count[0] == 1


# ══════════════════════════════════════════════════════════════════════════════
# TaskLedger integration
# ══════════════════════════════════════════════════════════════════════════════


class TestLedgerIntegration:
    def test_ledger_entry_created_on_success(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        def fn() -> str:
            return "ok"

        fn()

        tasks = ledger.list()
        assert len(tasks) == 1
        task = tasks[0]
        assert task.title == "verified:fn"
        assert task.status == TaskStatus.DONE

    def test_ledger_entry_marked_failed_on_raise(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="raise", ledger=ledger)
        def fn() -> str:
            return "output"

        with pytest.raises(VerificationError):
            fn()

        tasks = ledger.list()
        assert len(tasks) == 1
        assert tasks[0].status in {TaskStatus.FAILED, TaskStatus.ABANDONED}

    def test_ledger_result_contains_output(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        def fn() -> str:
            return "my_output"

        fn()

        task = ledger.list()[0]
        assert task.result is not None
        assert task.result.raw_output == "my_output"

    def test_ledger_entry_created_on_log_fail(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysFailVerifier()], on_fail="log", ledger=ledger)
        def fn() -> str:
            return "output"

        fn()

        tasks = ledger.list()
        assert len(tasks) == 1
        assert tasks[0].status in {TaskStatus.FAILED, TaskStatus.ABANDONED}

    def test_ephemeral_ledger_created_when_none_given(self):
        """No ledger arg → ephemeral ledger, function still works."""

        @verified(verifiers=[AlwaysPassVerifier()])
        def fn() -> str:
            return "ok"

        assert fn() == "ok"

    def test_async_ledger_entry_created(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        async def fn() -> str:
            return "async_ok"

        asyncio.run(fn())

        tasks = ledger.list()
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.DONE


# ══════════════════════════════════════════════════════════════════════════════
# Provenance tokens
# ══════════════════════════════════════════════════════════════════════════════


class TestProvenanceTokens:
    def test_provenance_token_in_result(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        def fn() -> str:
            return "hello"

        fn()

        task = ledger.list()[0]
        assert task.result is not None
        token = task.result.structured.get("_provenance_token")
        assert token is not None
        assert isinstance(token, str)
        assert len(token) == 64  # SHA-256 hex = 64 chars

    def test_provenance_token_is_deterministic_structure(self):
        """Same inputs produce same hash."""
        t1 = _generate_provenance("fn", "task-1", 1700000000.0, "hello")
        t2 = _generate_provenance("fn", "task-1", 1700000000.0, "hello")
        assert t1 == t2

    def test_provenance_token_differs_by_result(self):
        """Different results produce different tokens."""
        t1 = _generate_provenance("fn", "task-1", 1700000000.0, "hello")
        t2 = _generate_provenance("fn", "task-1", 1700000000.0, "world")
        assert t1 != t2

    def test_provenance_token_differs_by_timestamp(self):
        """Different timestamps produce different tokens."""
        t1 = _generate_provenance("fn", "task-1", 1700000000.0, "hello")
        t2 = _generate_provenance("fn", "task-1", 1700000001.0, "hello")
        assert t1 != t2

    def test_async_provenance_token_in_result(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        async def fn() -> str:
            return "async_hello"

        asyncio.run(fn())

        task = ledger.list()[0]
        token = task.result.structured.get("_provenance_token")  # type: ignore[union-attr]
        assert token is not None
        assert len(token) == 64


# ══════════════════════════════════════════════════════════════════════════════
# Custom metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestMetadata:
    def test_metadata_stored_in_task(self, ledger: TaskLedger):
        @verified(
            verifiers=[AlwaysPassVerifier()],
            metadata={"agent": "test_agent", "version": "1.0"},
            ledger=ledger,
        )
        def fn() -> str:
            return "ok"

        fn()

        task = ledger.list()[0]
        assert task.metadata["agent"] == "test_agent"
        assert task.metadata["version"] == "1.0"

    def test_empty_metadata_allowed(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], metadata={}, ledger=ledger)
        def fn() -> str:
            return "ok"

        fn()

        tasks = ledger.list()
        assert len(tasks) == 1

    def test_metadata_none_defaults_to_empty(self, ledger: TaskLedger):
        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        def fn() -> str:
            return "ok"

        fn()

        task = ledger.list()[0]
        # Should not crash; metadata may contain internal keys but no custom ones
        assert "agent" not in task.metadata


class TestEphemeralLedgerCleanup:
    def test_sync_ephemeral_ledger_cleanup_on_success(self, tmp_path: Path, monkeypatch) -> None:
        temp_dir = tmp_path / "sync_success"

        def fake_make_ephemeral_ledger() -> tuple[TaskLedger, str]:
            temp_dir.mkdir(parents=True, exist_ok=True)
            ledger = TaskLedger(
                path=temp_dir / "ledger.json",
                progress_file=str(temp_dir / "progress.md"),
            )
            return ledger, str(temp_dir)

        monkeypatch.setattr(decorator_module, "_make_ephemeral_ledger", fake_make_ephemeral_ledger)

        @verified(verifiers=[AlwaysPassVerifier()])
        def fn() -> str:
            return "ok"

        assert fn() == "ok"
        assert not temp_dir.exists()

    def test_sync_ephemeral_ledger_cleanup_on_exception(self, tmp_path: Path, monkeypatch) -> None:
        temp_dir = tmp_path / "sync_exception"

        def fake_make_ephemeral_ledger() -> tuple[TaskLedger, str]:
            temp_dir.mkdir(parents=True, exist_ok=True)
            ledger = TaskLedger(
                path=temp_dir / "ledger.json",
                progress_file=str(temp_dir / "progress.md"),
            )
            return ledger, str(temp_dir)

        monkeypatch.setattr(decorator_module, "_make_ephemeral_ledger", fake_make_ephemeral_ledger)

        @verified(verifiers=[AlwaysPassVerifier()])
        def fn() -> str:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            fn()
        assert not temp_dir.exists()

    def test_async_ephemeral_ledger_cleanup_on_success(self, tmp_path: Path, monkeypatch) -> None:
        temp_dir = tmp_path / "async_success"

        def fake_make_ephemeral_ledger() -> tuple[TaskLedger, str]:
            temp_dir.mkdir(parents=True, exist_ok=True)
            ledger = TaskLedger(
                path=temp_dir / "ledger.json",
                progress_file=str(temp_dir / "progress.md"),
            )
            return ledger, str(temp_dir)

        monkeypatch.setattr(decorator_module, "_make_ephemeral_ledger", fake_make_ephemeral_ledger)

        @verified(verifiers=[AlwaysPassVerifier()])
        async def fn() -> str:
            return "ok"

        assert asyncio.run(fn()) == "ok"
        assert not temp_dir.exists()

    def test_external_ledger_not_cleaned(self, tmp_path: Path) -> None:
        ledger_dir = tmp_path / "external_ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger = TaskLedger(
            path=ledger_dir / "ledger.json",
            progress_file=str(ledger_dir / "progress.md"),
        )

        @verified(verifiers=[AlwaysPassVerifier()], ledger=ledger)
        def fn() -> str:
            return "ok"

        assert fn() == "ok"
        assert ledger_dir.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Built-in decorator verifiers
# ══════════════════════════════════════════════════════════════════════════════


class TestBuiltinDecoratorVerifiers:
    def _task(self) -> Task:
        return Task(title="test", metadata={})

    def _result(self, val: Any) -> TaskResult:
        return _make_task_result(val)

    def test_not_none_passes_on_value(self):
        vr = NotNoneVerifier().verify(self._task(), self._result("hello"))
        assert vr.passed is True

    def test_not_none_fails_on_none(self):
        vr = NotNoneVerifier().verify(self._task(), self._result(None))
        assert vr.passed is False
        assert "None" in (vr.error or "")

    def test_not_empty_passes_on_non_empty_string(self):
        vr = NotEmptyVerifier().verify(self._task(), self._result("hello"))
        assert vr.passed is True

    def test_not_empty_fails_on_empty_string(self):
        vr = NotEmptyVerifier().verify(self._task(), self._result(""))
        assert vr.passed is False

    def test_not_empty_fails_on_empty_list(self):
        vr = NotEmptyVerifier().verify(self._task(), self._result([]))
        assert vr.passed is False

    def test_not_empty_passes_on_non_empty_list(self):
        vr = NotEmptyVerifier().verify(self._task(), self._result([1, 2]))
        assert vr.passed is True

    def test_type_check_passes_when_annotation_matches(self):
        task = Task(title="test", metadata={"_return_annotation": str})
        vr = TypeCheckVerifier().verify(task, self._result("hello"))
        assert vr.passed is True

    def test_type_check_fails_when_annotation_mismatches(self):
        task = Task(title="test", metadata={"_return_annotation": str})
        vr = TypeCheckVerifier().verify(task, self._result(42))
        assert vr.passed is False
        assert "str" in (vr.error or "")

    def test_type_check_passes_when_no_annotation(self):
        task = Task(title="test", metadata={})
        vr = TypeCheckVerifier().verify(task, self._result(42))
        assert vr.passed is True

    def test_type_check_with_explicit_expected_type(self):
        verifier = TypeCheckVerifier(expected_type=int)
        vr = verifier.verify(self._task(), self._result(42))
        assert vr.passed is True

        vr2 = verifier.verify(self._task(), self._result("not int"))
        assert vr2.passed is False


# ══════════════════════════════════════════════════════════════════════════════
# _make_task_result helper
# ══════════════════════════════════════════════════════════════════════════════


class TestMakeTaskResult:
    def test_string_value(self):
        tr = _make_task_result("hello")
        assert tr.raw_output == "hello"
        assert tr.structured["_return_value"] == "hello"

    def test_none_value(self):
        tr = _make_task_result(None)
        assert tr.raw_output == ""
        assert tr.structured["_return_value"] is None

    def test_dict_value_merged_into_structured(self):
        tr = _make_task_result({"key": "val"})
        assert tr.structured["key"] == "val"
        assert tr.structured["_return_value"] == {"key": "val"}

    def test_int_value(self):
        tr = _make_task_result(42)
        assert tr.raw_output == "42"
        assert tr.structured["_return_value"] == 42


# ══════════════════════════════════════════════════════════════════════════════
# _resolve_verifiers helper
# ══════════════════════════════════════════════════════════════════════════════


class TestResolveVerifiers:
    def test_none_returns_defaults(self):
        result = _resolve_verifiers(None, str)
        ids = [v.id for v in result]
        assert "not_none" in ids
        assert "type_check" in ids

    def test_instance_list_passed_through(self):
        v = AlwaysPassVerifier()
        result = _resolve_verifiers([v], None)
        assert result == [v]

    def test_string_ids_resolved(self):
        result = _resolve_verifiers(["not_none"], None)
        assert len(result) == 1
        assert result[0].id == "not_none"

    def test_empty_list_returns_empty(self):
        result = _resolve_verifiers([], None)
        assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# Invalid configuration
# ══════════════════════════════════════════════════════════════════════════════


class TestInvalidConfig:
    def test_invalid_on_fail_raises_value_error(self):
        with pytest.raises(ValueError, match="on_fail"):
            verified(on_fail="invalid")

    def test_max_retries_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="max_retries"):
            verified(max_retries=0)

    def test_function_exception_propagates(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        def fn() -> str:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            fn()

    def test_async_function_exception_propagates(self):
        @verified(verifiers=[AlwaysPassVerifier()])
        async def fn() -> str:
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            asyncio.run(fn())
