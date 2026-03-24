"""
veridian.verify.builtin.http
─────────────────────────────
HttpStatusVerifier — make an HTTP request and verify the response status code.

Usage:
    verifier_id="http_status"
    verifier_config={
        "url": "https://api.example.com/health",
        "expected_statuses": [200, 201],
        "timeout_seconds": 10,
    }
"""
from __future__ import annotations

from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


class HttpStatusVerifier(BaseVerifier):
    """
    Make an HTTP GET request to url and pass if status_code is in expected_statuses.

    Stateless: all config is in constructor. Connection errors return failed result.
    """

    id: ClassVar[str] = "http_status"
    description: ClassVar[str] = (
        "Make an HTTP GET request and verify the response status code "
        "is in the expected list."
    )

    def __init__(
        self,
        url: str,
        expected_statuses: list[int] | None = None,
        timeout_seconds: int = 10,
        method: str = "GET",
    ) -> None:
        """
        Args:
            url: The URL to request. Must be non-empty.
            expected_statuses: Acceptable HTTP status codes. Defaults to [200].
            timeout_seconds: Request timeout in seconds.
            method: HTTP method. Default GET.
        """
        if not url or not url.strip():
            raise VeridianConfigError(
                "HttpStatusVerifier: 'url' must not be empty. "
                "Provide a fully-qualified URL, e.g. 'https://api.example.com/health'."
            )
        if timeout_seconds <= 0:
            raise VeridianConfigError(
                f"HttpStatusVerifier: 'timeout_seconds' must be > 0, got {timeout_seconds}."
            )
        self.url = url
        self.expected_statuses: list[int] = expected_statuses if expected_statuses else [200]
        self.timeout_seconds = timeout_seconds
        self.method = method.upper()

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Make HTTP request and check status code."""
        import httpx  # noqa: PLC0415

        try:
            if self.method == "GET":
                resp = httpx.get(self.url, timeout=self.timeout_seconds)
            elif self.method == "HEAD":
                resp = httpx.head(self.url, timeout=self.timeout_seconds)
            else:
                resp = httpx.request(self.method, self.url, timeout=self.timeout_seconds)
        except httpx.TimeoutException:
            return VerificationResult(
                passed=False,
                error=(
                    f"HTTP {self.method} {self.url} timed out after {self.timeout_seconds}s. "
                    f"Check URL reachability or increase timeout_seconds."
                )[:300],
            )
        except Exception as exc:
            return VerificationResult(
                passed=False,
                error=f"HTTP {self.method} {self.url} failed: {str(exc)[:150]}"[:300],
            )

        actual = resp.status_code
        if actual in self.expected_statuses:
            return VerificationResult(
                passed=True,
                evidence={
                    "url": self.url,
                    "status_code": actual,
                    "expected_statuses": self.expected_statuses,
                },
            )

        expected_str = str(self.expected_statuses[0]) if len(self.expected_statuses) == 1 \
            else str(self.expected_statuses)
        return VerificationResult(
            passed=False,
            error=(
                f"HTTP {actual} from {self.url} (expected {expected_str}). "
                f"Check the endpoint is reachable and returning the expected status."
            )[:300],
            evidence={
                "url": self.url,
                "actual_status": actual,
                "expected_statuses": self.expected_statuses,
            },
        )
