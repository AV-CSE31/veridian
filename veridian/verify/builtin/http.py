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

import ipaddress
import os
from typing import ClassVar
from urllib.parse import urlparse

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    # AWS / GCP / Azure metadata endpoint
    ipaddress.IPv4Network("169.254.0.0/16"),
)


def _is_blocked_host(hostname: str) -> bool:
    """Return True if ``hostname`` resolves to a private / loopback / link-local
    address that an HTTP verifier should not contact by default.

    This is a defence against SSRF where a task config could otherwise be
    weaponised to probe internal services or cloud metadata endpoints.
    Hostnames that don't parse as IPs are not blocked at this layer (we do
    not perform DNS to keep the verifier stateless); operators relying on
    this guard should still keep their hostnames pinned to public services.
    """
    blocked_literals = {"localhost", "::1"}
    if hostname.lower() in blocked_literals:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_NETWORKS)
    return ip.is_loopback or ip.is_link_local or ip.is_private


class HttpStatusVerifier(BaseVerifier):
    """
    Make an HTTP GET request to url and pass if status_code is in expected_statuses.

    Stateless: all config is in constructor. Connection errors return failed result.
    """

    id: ClassVar[str] = "http_status"
    description: ClassVar[str] = (
        "Make an HTTP GET request and verify the response status code is in the expected list."
    )

    def __init__(
        self,
        url: str,
        expected_statuses: list[int] | None = None,
        timeout_seconds: int = 10,
        method: str = "GET",
        allow_private_targets: bool = False,
    ) -> None:
        """
        Args:
            url: The URL to request. Must be non-empty.
            expected_statuses: Acceptable HTTP status codes. Defaults to [200].
            timeout_seconds: Request timeout in seconds.
            method: HTTP method. Default GET.
            allow_private_targets: When ``False`` (default) the verifier
                rejects URLs whose host is a loopback / private / link-local
                address. Set to ``True`` (or export
                ``VERIDIAN_HTTP_ALLOW_PRIVATE=1``) for intentional internal
                probes. Defence-in-depth against SSRF via task config.
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
        env_allow = os.getenv("VERIDIAN_HTTP_ALLOW_PRIVATE", "").strip() == "1"
        if not (allow_private_targets or env_allow):
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if _is_blocked_host(host):
                raise VeridianConfigError(
                    f"HttpStatusVerifier refuses to target host {host!r} — "
                    "loopback / private / link-local addresses are blocked "
                    "to prevent SSRF. Pass allow_private_targets=True or "
                    "set VERIDIAN_HTTP_ALLOW_PRIVATE=1 to opt in."
                )
        self.url = url
        self.expected_statuses: list[int] = expected_statuses if expected_statuses else [200]
        self.timeout_seconds = timeout_seconds
        self.method = method.upper()
        self.allow_private_targets = allow_private_targets

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Make HTTP request and check status code."""
        return self._verify_direct(task, result)

    def _verify_direct(self, task: Task, result: TaskResult) -> VerificationResult:
        """Direct HTTP call path."""
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
        return self._evaluate_status(actual)

    def _evaluate_status(self, actual: int) -> VerificationResult:
        """Evaluate HTTP status code against expected statuses."""
        if actual in self.expected_statuses:
            return VerificationResult(
                passed=True,
                evidence={
                    "url": self.url,
                    "status_code": actual,
                    "expected_statuses": self.expected_statuses,
                },
            )

        expected_str = (
            str(self.expected_statuses[0])
            if len(self.expected_statuses) == 1
            else str(self.expected_statuses)
        )
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
