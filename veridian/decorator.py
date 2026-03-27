"""
veridian.decorator
──────────────────
@verified — zero-friction decorator for wrapping agent functions with Veridian
verification.

Usage::

    # Zero-config — just works
    from veridian import verified

    @verified
    async def my_agent(task: str) -> str:
        return result

    # With options
    @verified(
        verifiers=["type_check", "schema", "not_empty"],
        on_fail="raise",          # or "log", "retry"
        max_retries=3,
        metadata={"agent": "my_agent", "version": "1.0"},
    )
    async def my_agent(task: str) -> str:
        return result
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import logging
import tempfile
import time
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from veridian.core.exceptions import VerificationError
from veridian.core.task import Task, TaskResult
from veridian.ledger.ledger import TaskLedger
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.base import registry as verifier_registry

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_SENTINEL = object()  # distinguishes @verified from @verified()

# ── Decorator-specific verifiers ──────────────────────────────────────────────


class NotNoneVerifier(BaseVerifier):
    """Verify the return value is not None."""

    id = "not_none"
    description = "Verify that the result is not None."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        val = result.structured.get("_return_value", _SENTINEL)
        if val is _SENTINEL:
            # Fallback: check raw_output
            if result.raw_output == "" or result.raw_output == "None":
                return VerificationResult(passed=False, error="Result is None")
            return VerificationResult(passed=True)
        if val is None:
            return VerificationResult(passed=False, error="Result is None")
        return VerificationResult(passed=True)


class NotEmptyVerifier(BaseVerifier):
    """Verify the return value is not None or an empty container."""

    id = "not_empty"
    description = "Verify that the result is not None or empty."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        val = result.structured.get("_return_value", _SENTINEL)
        if val is _SENTINEL:
            val = result.raw_output or None
        if val is None:
            return VerificationResult(passed=False, error="Result is None")
        if isinstance(val, (str, list, dict, tuple, set)) and not val:
            return VerificationResult(
                passed=False,
                error=f"Result is an empty {type(val).__name__}",
            )
        return VerificationResult(passed=True)


class TypeCheckVerifier(BaseVerifier):
    """Verify the return value matches the function's return type annotation."""

    id = "type_check"
    description = "Verify that the result matches the function's return type annotation."

    def __init__(self, expected_type: type | None = None) -> None:
        self.expected_type = expected_type

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        expected = self.expected_type or task.metadata.get("_return_annotation")
        # Skip if no annotation, annotation is a string (unresolved), or not a plain type
        if expected is None or expected is type(None):
            return VerificationResult(
                passed=True,
                evidence={"note": "no return type annotation to check"},
            )
        if isinstance(expected, str):
            return VerificationResult(
                passed=True,
                evidence={"note": "annotation is unresolved string — skipped"},
            )
        # Only check plain types (not generics like List[str])
        if not isinstance(expected, type):
            return VerificationResult(
                passed=True,
                evidence={"note": "complex annotation — skipped"},
            )
        val = result.structured.get("_return_value", _SENTINEL)
        if val is _SENTINEL:
            return VerificationResult(passed=True, evidence={"note": "no structured value"})
        if val is None:
            # None satisfies Optional[T] — let not_none catch None failures
            return VerificationResult(passed=True)
        try:
            passes = isinstance(val, expected)
        except TypeError:
            return VerificationResult(
                passed=True,
                evidence={"note": f"isinstance check skipped for {expected!r}"},
            )
        if not passes:
            return VerificationResult(
                passed=False,
                error=(f"Expected return type {expected.__name__}, got {type(val).__name__}"),
            )
        return VerificationResult(passed=True)


# Register decorator verifiers (idempotent — duplicate register is a no-op via log)
for _cls in (NotNoneVerifier, NotEmptyVerifier, TypeCheckVerifier):
    if _cls.id not in verifier_registry._classes:  # noqa: SLF001
        verifier_registry.register(_cls)


# ── Provenance token ──────────────────────────────────────────────────────────


def _generate_provenance(
    func_name: str,
    task_id: str,
    timestamp: float,
    result_repr: str,
) -> str:
    """SHA-256 hash binding execution identity to result snapshot."""
    payload = f"{func_name}|{task_id}|{timestamp:.6f}|{result_repr[:500]}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Result → TaskResult ───────────────────────────────────────────────────────


def _make_task_result(return_value: Any) -> TaskResult:
    """Wrap a function's return value in a TaskResult."""
    structured: dict[str, Any] = {"_return_value": return_value}
    if isinstance(return_value, dict):
        structured.update(return_value)
    raw_output = "" if return_value is None else str(return_value)
    return TaskResult(raw_output=raw_output, structured=structured)


# ── Verifier resolution ───────────────────────────────────────────────────────


def _resolve_verifiers(
    verifiers: list[str | BaseVerifier] | None,
    return_annotation: Any,
) -> list[BaseVerifier]:
    """Turn verifier names/instances into BaseVerifier objects.

    When *verifiers* is None the defaults are used.  The ``type_check``
    default is constructed with *return_annotation* pre-bound so that it
    never needs to touch task.metadata (task metadata must be JSON-safe).
    """
    if verifiers is None:
        resolved: list[BaseVerifier] = []
        # not_none — always include
        try:
            resolved.append(verifier_registry.get("not_none"))
        except Exception:  # pragma: no cover
            log.warning("verified: could not load default verifier 'not_none'")
        # type_check — pre-bind the annotation so metadata stays JSON-safe
        resolved.append(TypeCheckVerifier(expected_type=return_annotation))
        return resolved

    out: list[BaseVerifier] = []
    for v in verifiers:
        if isinstance(v, BaseVerifier):
            out.append(v)
        elif isinstance(v, str):
            try:
                out.append(verifier_registry.get(v))
            except Exception as exc:
                log.warning("verified: unknown verifier %r — skipped (%s)", v, exc)
    return out


# ── Ephemeral ledger factory ──────────────────────────────────────────────────


def _make_ephemeral_ledger() -> tuple[TaskLedger, str]:
    """Return (ledger, tmp_dir).  Caller owns cleanup."""
    tmp_dir = tempfile.mkdtemp(prefix="veridian_verified_")
    ledger = TaskLedger(
        path=Path(tmp_dir) / "ledger.json",
        progress_file=str(Path(tmp_dir) / "progress.md"),
    )
    return ledger, tmp_dir


# ── Core sync execution ───────────────────────────────────────────────────────


def _execute_sync(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    verifiers_list: list[BaseVerifier],
    on_fail: str,
    max_retries: int,
    metadata: dict[str, Any],
    ledger: TaskLedger | None,
) -> Any:
    own_ledger = ledger is None
    _tmp_dir: str | None = None
    if own_ledger:
        _ledger, _tmp_dir = _make_ephemeral_ledger()
    else:
        _ledger = ledger  # type: ignore[assignment]

    task = Task(
        title=f"verified:{func.__name__}",
        description=f"@verified execution of {func.__qualname__}",
        verifier_id="not_none",
        max_retries=max(max_retries + 5, 10),  # prevent ledger auto-abandon
        metadata=metadata,
    )
    _ledger.add([task])
    _ledger.claim(task.id, runner_id="verified-decorator")

    attempt = 0
    while True:
        attempt += 1
        timestamp = time.time()

        try:
            return_value = func(*args, **kwargs)
        except Exception as exc:
            _ledger.mark_failed(task.id, error=str(exc))
            raise

        task_result = _make_task_result(return_value)
        provenance = _generate_provenance(func.__name__, task.id, timestamp, repr(return_value))
        task_result.structured["_provenance_token"] = provenance

        # Run verifiers
        all_passed, fail_errors = _run_verifiers(verifiers_list, task, task_result)

        if all_passed:
            _ledger.submit_result(task.id, task_result)
            _ledger.mark_done(task.id, task_result)
            return return_value

        # Verification failed
        _handle_failure(
            _ledger,
            task,
            task_result,
            func.__name__,
            fail_errors,
            on_fail,
            max_retries,
            attempt,
        )

        if on_fail in ("raise", "retry") and attempt >= max_retries:
            # _handle_failure already raised for on_fail=="raise"
            # For "retry" exhaustion, it raises too — unreachable here
            break  # pragma: no cover
        if on_fail == "log":
            return return_value


# ── Core async execution ──────────────────────────────────────────────────────


async def _execute_async(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    verifiers_list: list[BaseVerifier],
    on_fail: str,
    max_retries: int,
    metadata: dict[str, Any],
    ledger: TaskLedger | None,
) -> Any:
    own_ledger = ledger is None
    _tmp_dir: str | None = None
    if own_ledger:
        _ledger, _tmp_dir = _make_ephemeral_ledger()
    else:
        _ledger = ledger  # type: ignore[assignment]

    task = Task(
        title=f"verified:{func.__name__}",
        description=f"@verified execution of {func.__qualname__}",
        verifier_id="not_none",
        max_retries=max(max_retries + 5, 10),
        metadata=metadata,
    )
    _ledger.add([task])
    _ledger.claim(task.id, runner_id="verified-decorator")

    attempt = 0
    while True:
        attempt += 1
        timestamp = time.time()

        try:
            return_value = await func(*args, **kwargs)
        except Exception as exc:
            _ledger.mark_failed(task.id, error=str(exc))
            raise

        task_result = _make_task_result(return_value)
        provenance = _generate_provenance(func.__name__, task.id, timestamp, repr(return_value))
        task_result.structured["_provenance_token"] = provenance

        all_passed, fail_errors = _run_verifiers(verifiers_list, task, task_result)

        if all_passed:
            _ledger.submit_result(task.id, task_result)
            _ledger.mark_done(task.id, task_result)
            return return_value

        _handle_failure(
            _ledger,
            task,
            task_result,
            func.__name__,
            fail_errors,
            on_fail,
            max_retries,
            attempt,
        )

        if on_fail == "log":
            return return_value


# ── Shared helpers ────────────────────────────────────────────────────────────


def _run_verifiers(
    verifiers_list: list[BaseVerifier],
    task: Task,
    task_result: TaskResult,
) -> tuple[bool, list[str]]:
    """Run all verifiers. Returns (all_passed, list_of_error_messages)."""
    all_passed = True
    fail_errors: list[str] = []
    ver_summary: list[dict[str, Any]] = []

    for verifier in verifiers_list:
        try:
            vr = verifier.verify(task, task_result)
        except Exception as exc:
            vr = VerificationResult(passed=False, error=f"Verifier {verifier.id!r} raised: {exc}")
        ver_summary.append(
            {
                "verifier": getattr(verifier, "id", "?"),
                "passed": vr.passed,
                "error": vr.error,
            }
        )
        if not vr.passed:
            all_passed = False
            if vr.error:
                fail_errors.append(vr.error)

    task_result.structured["_verification_summary"] = ver_summary
    return all_passed, fail_errors


def _handle_failure(
    ledger: TaskLedger,
    task: Task,
    task_result: TaskResult,
    func_name: str,
    fail_errors: list[str],
    on_fail: str,
    max_retries: int,
    attempt: int,
) -> None:
    """Handle a failed verification according to on_fail policy."""
    error_msg = "; ".join(fail_errors) if fail_errors else "verification failed"

    if on_fail == "raise":
        ledger.submit_result(task.id, task_result)
        ledger.mark_failed(task.id, error=error_msg)
        raise VerificationError(f"@verified: {func_name!r} failed verification — {error_msg}")

    if on_fail == "log":
        log.warning(
            "@verified: %r failed verification (logged, not raised) — %s",
            func_name,
            error_msg,
        )
        ledger.submit_result(task.id, task_result)
        ledger.mark_failed(task.id, error=error_msg)
        return

    if on_fail == "retry":
        if attempt >= max_retries:
            ledger.submit_result(task.id, task_result)
            ledger.mark_failed(task.id, error=error_msg)
            raise VerificationError(
                f"@verified: {func_name!r} failed after {attempt} "
                f"{'retry' if attempt == 1 else 'retries'} — {error_msg}"
            )
        log.info(
            "@verified: retry %d/%d for %r — %s",
            attempt,
            max_retries,
            func_name,
            error_msg,
        )
        # Reset for next attempt: mark failed, then reset back to PENDING
        ledger.submit_result(task.id, task_result)
        ledger.mark_failed(task.id, error=error_msg)
        ledger.reset_failed([task.id])
        ledger.claim(task.id, runner_id="verified-decorator")


# ── Public decorator factory ──────────────────────────────────────────────────


def verified(
    func: F | None = None,
    *,
    verifiers: list[str | BaseVerifier] | None = None,
    on_fail: str = "raise",
    max_retries: int = 1,
    metadata: dict[str, Any] | None = None,
    ledger: TaskLedger | None = None,
) -> F | Callable[[F], F]:
    """
    Decorator that wraps any agent function with Veridian verification.

    Can be used with or without arguments::

        @verified
        def fn() -> str: ...

        @verified(verifiers=["not_empty"], on_fail="log")
        def fn() -> str: ...

    Parameters
    ----------
    verifiers:
        List of verifier IDs (str) or ``BaseVerifier`` instances.
        Defaults to ``["not_none", "type_check"]``.
    on_fail:
        ``"raise"`` (default) — raises ``VerificationError`` on failure.
        ``"log"``   — logs the failure and returns the value anyway.
        ``"retry"`` — re-executes up to ``max_retries`` times before raising.
    max_retries:
        Max attempts when ``on_fail="retry"``.  Ignored otherwise.
    metadata:
        Extra key/value pairs stored in the TaskLedger entry.
    ledger:
        Existing ``TaskLedger`` to record into.  Creates an ephemeral
        temp-file ledger if omitted.
    """
    if on_fail not in ("raise", "log", "retry"):
        raise ValueError(f"on_fail must be 'raise', 'log', or 'retry', got {on_fail!r}")
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")

    _metadata: dict[str, Any] = metadata or {}

    def decorator(fn: F) -> F:
        # Resolve return annotation once at decoration time.
        # Use get_type_hints() to resolve string annotations (PEP 563 / __future__).
        try:
            hints = typing.get_type_hints(fn)
        except Exception:
            hints = getattr(fn, "__annotations__", {})
        return_annotation: Any = hints.get("return", None)

        verifiers_list = _resolve_verifiers(verifiers, return_annotation)

        common: dict[str, Any] = dict(
            verifiers_list=verifiers_list,
            on_fail=on_fail,
            max_retries=max_retries,
            metadata=_metadata,
            ledger=ledger,
        )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await _execute_async(fn, args, kwargs, **common)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return _execute_sync(fn, args, kwargs, **common)

        return sync_wrapper  # type: ignore[return-value]

    # Support both @verified and @verified(...)
    if func is not None:
        # Called as @verified (no parentheses)
        return decorator(func)

    # Called as @verified(...) — return the decorator
    return decorator
