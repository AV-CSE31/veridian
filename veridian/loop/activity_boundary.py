"""
veridian.loop.activity_boundary
─────────────────────────────────
WCP-010: Typed activity wrappers for ALL external side effects.

Every external call (HTTP, file I/O, subprocess) MUST be routed through an
activity boundary so the ActivityJournal records it for deterministic replay,
deduplication, and audit.

Each wrapper:
  1. Checks if ``activity_id`` already completed (replay) — returns cached result.
  2. Otherwise executes the operation and records the outcome.
  3. Uses ``run_activity`` from the core activity module for journal integration.

Usage::

    journal = ActivityJournal()

    # HTTP
    result = http_activity(journal, "check_health", "GET", "https://api.example.com/health")

    # File
    result = file_activity(journal, "verify_output", "/tmp/report.json", "exists")

    # Subprocess
    result = subprocess_activity(journal, "run_lint", ["ruff", "check", "."])
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import httpx

from veridian.core.exceptions import VeridianError
from veridian.loop.activity import ActivityJournal, RetryPolicy, run_activity

__all__ = [
    "ActivityBoundaryError",
    "file_activity",
    "http_activity",
    "subprocess_activity",
]

log = logging.getLogger(__name__)


class ActivityBoundaryError(VeridianError):
    """Raised when an activity boundary wrapper encounters a configuration error.

    This is NOT raised for runtime failures (those go through ActivityError via
    run_activity's retry logic). This covers invalid arguments like unsupported
    file operations.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Activity boundary error: {detail}")


# ── HTTP Activity ────────────────────────────────────────────────────────────


def _execute_http(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Execute an HTTP request and return a serializable result dict."""
    response = httpx.request(method, url, **kwargs)
    return {
        "status_code": response.status_code,
        "text": response.text,
        "headers": dict(response.headers),
    }


def http_activity(
    *,
    journal: ActivityJournal,
    activity_id: str,
    method: str,
    url: str,
    retry_policy: RetryPolicy | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Wrap an HTTP call in an activity boundary.

    Args:
        journal: The activity journal to record this call in.
        activity_id: Stable identifier for this activity (used as idempotency key).
        method: HTTP method (GET, POST, HEAD, etc.).
        url: Target URL.
        retry_policy: Optional retry configuration. Defaults to single attempt.
        **kwargs: Passed through to ``httpx.request()`` (timeout, headers, etc.).

    Returns:
        Dict with ``status_code``, ``text``, and ``headers`` keys.

    Raises:
        ActivityError: If all retry attempts fail.
    """
    policy = retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=0.0)

    result: Any = run_activity(
        journal=journal,
        fn=_execute_http,
        args=(method, url),
        kwargs=kwargs,
        fn_name="http_request",
        idempotency_key=activity_id,
        retry_policy=policy,
    )
    return result  # type: ignore[no-any-return]


# ── File Activity ────────────────────────────────────────────────────────────


_SUPPORTED_FILE_OPS = frozenset({"exists", "read", "stat"})


def _execute_file_check(path_str: str, operation: str) -> dict[str, Any]:
    """Execute a file check operation and return a serializable result dict."""
    p = Path(path_str)

    if operation == "exists":
        exists = p.exists()
        size = p.stat().st_size if exists else None
        return {"exists": exists, "size": size, "path": path_str}

    if operation == "read":
        exists = p.exists()
        if not exists:
            return {"exists": False, "content": None, "size": None, "path": path_str}
        content = p.read_text(encoding="utf-8")
        size = p.stat().st_size
        return {"exists": True, "content": content, "size": size, "path": path_str}

    if operation == "stat":
        exists = p.exists()
        if not exists:
            return {"exists": False, "size": None, "mtime": None, "path": path_str}
        st = p.stat()
        return {
            "exists": True,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "path": path_str,
        }

    # Should not reach here — caller validates operation
    raise ActivityBoundaryError(f"Unsupported file operation: {operation!r}")  # pragma: no cover


def file_activity(
    *,
    journal: ActivityJournal,
    activity_id: str,
    path: str,
    operation: str,
    retry_policy: RetryPolicy | None = None,
) -> dict[str, Any]:
    """Wrap a file I/O check in an activity boundary.

    Args:
        journal: The activity journal to record this call in.
        activity_id: Stable identifier for this activity (used as idempotency key).
        path: File path to check.
        operation: One of ``"exists"``, ``"read"``, ``"stat"``.
        retry_policy: Optional retry configuration.

    Returns:
        Dict with operation-specific keys (``exists``, ``size``, ``content``, etc.).

    Raises:
        ActivityBoundaryError: If ``operation`` is not supported.
        ActivityError: If all retry attempts fail.
    """
    if operation not in _SUPPORTED_FILE_OPS:
        raise ActivityBoundaryError(
            f"Unsupported file operation: {operation!r}. Supported: {sorted(_SUPPORTED_FILE_OPS)}"
        )

    policy = retry_policy or RetryPolicy(max_attempts=1, backoff_seconds=0.0)

    result: Any = run_activity(
        journal=journal,
        fn=_execute_file_check,
        args=(path, operation),
        fn_name="file_check",
        idempotency_key=activity_id,
        retry_policy=policy,
    )
    return result  # type: ignore[no-any-return]


# ── Subprocess Activity ──────────────────────────────────────────────────────


def _execute_subprocess(
    cmd: list[str],
    timeout_seconds: int,
    cwd: str | None,
) -> dict[str, Any]:
    """Execute a subprocess and return a serializable result dict."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=cwd,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def subprocess_activity(
    *,
    journal: ActivityJournal,
    activity_id: str,
    cmd: list[str],
    timeout_seconds: int = 300,
    cwd: str | None = None,
    retry_policy: RetryPolicy | None = None,
) -> dict[str, Any]:
    """Wrap a subprocess call in an activity boundary.

    Args:
        journal: The activity journal to record this call in.
        activity_id: Stable identifier for this activity (used as idempotency key).
        cmd: Command and arguments as a list of strings.
        timeout_seconds: Maximum execution time. Default 300s.
        cwd: Working directory for the subprocess.
        retry_policy: Optional retry configuration.

    Returns:
        Dict with ``exit_code``, ``stdout``, and ``stderr`` keys.

    Raises:
        ActivityError: If all retry attempts fail (e.g. timeout).
    """
    policy = retry_policy or RetryPolicy(max_attempts=1, backoff_seconds=0.0)

    result: Any = run_activity(
        journal=journal,
        fn=_execute_subprocess,
        args=(cmd, timeout_seconds, cwd),
        fn_name="subprocess_exec",
        idempotency_key=activity_id,
        retry_policy=policy,
    )
    return result  # type: ignore[no-any-return]
