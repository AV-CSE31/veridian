"""
tests.unit.test_phase4_stretch
──────────────────────────────
Acceptance tests for Phase 4 stretch items:

* ``HttpStatusVerifier`` refuses URLs targeting loopback / private /
  link-local hosts by default, with an opt-in for intentional internal
  probes.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.verify.builtin.http import HttpStatusVerifier

# ── HttpStatusVerifier SSRF guard ───────────────────────────────────────────


class TestHttpVerifierSSRFGuard:
    def test_public_url_allowed(self) -> None:
        v = HttpStatusVerifier(url="https://api.example.com/health")
        assert v.url == "https://api.example.com/health"

    @pytest.mark.parametrize(
        "blocked_url",
        [
            "http://localhost/probe",
            "http://127.0.0.1:8080/admin",
            "http://10.0.0.1/",
            "http://172.16.0.10/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        ],
    )
    def test_private_targets_rejected_by_default(self, blocked_url: str) -> None:
        with pytest.raises(VeridianConfigError, match="SSRF"):
            HttpStatusVerifier(url=blocked_url)

    def test_constructor_opt_in_allows_private(self) -> None:
        v = HttpStatusVerifier(
            url="http://localhost:8080/health",
            allow_private_targets=True,
        )
        assert v.url == "http://localhost:8080/health"

    def test_env_opt_in_allows_private(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_HTTP_ALLOW_PRIVATE": "1"}):
            v = HttpStatusVerifier(url="http://127.0.0.1/internal")
        assert v.url == "http://127.0.0.1/internal"

    def test_non_ip_hostname_passes_guard(self) -> None:
        # Hostnames that don't parse as IPs are not blocked at this layer
        # (DNS would be needed). Operators still need to pin to public
        # services for the guard to be meaningful.
        v = HttpStatusVerifier(url="https://example.internal.corp/api")
        assert v.url == "https://example.internal.corp/api"


