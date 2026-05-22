"""Tests for the provider-agnostic RetryPolicy."""

from __future__ import annotations

import pytest

from veridian.core.exceptions import ProviderError
from veridian.providers.retry import (
    RetryPolicy,
    TenacityRetryPolicy,
    _default_policy_for,
    default_is_retryable,
)


class TestDefaultIsRetryable:
    @pytest.mark.parametrize(
        "msg",
        [
            "429 Too Many Requests",
            "503 Service Unavailable",
            "Connection reset by peer",
            "Read timeout",
            "Server overloaded",
        ],
    )
    def test_transient_errors_are_retryable(self, msg: str) -> None:
        assert default_is_retryable(RuntimeError(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "401 Unauthorized",
            "403 Forbidden",
            "404 Not Found",
            "400 Bad Request",
        ],
    )
    def test_permanent_errors_are_not_retryable(self, msg: str) -> None:
        assert default_is_retryable(RuntimeError(msg)) is False


class TestRetryPolicyShape:
    def test_default_field_values(self) -> None:
        policy = RetryPolicy()
        assert policy.max_attempts == 4
        assert policy.min_backoff_seconds == 1.0
        assert policy.max_backoff_seconds == 30.0
        assert policy.is_retryable is default_is_retryable

    def test_base_class_retrying_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            RetryPolicy().retrying()

    def test_default_policy_helper_maps_legacy_kwargs(self) -> None:
        policy = _default_policy_for(
            max_retries=5,
            min_backoff=0.5,
            max_backoff=10.0,
            jitter=1.5,
        )
        # max_attempts = max(1, max_retries + 1)
        assert policy.max_attempts == 6
        assert policy.min_backoff_seconds == 0.5
        assert policy.max_backoff_seconds == 10.0
        assert policy.jitter_seconds == 1.5


class TestTenacityRetryPolicy:
    def test_builds_a_retrying_object(self) -> None:
        pytest.importorskip("tenacity")
        from tenacity import Retrying  # noqa: PLC0415

        retrying = TenacityRetryPolicy(max_attempts=2).retrying()
        assert isinstance(retrying, Retrying)

    def test_raises_provider_error_when_tenacity_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the lazy ``import tenacity`` to fail by hiding the module.
        import sys

        monkeypatch.setitem(sys.modules, "tenacity", None)
        with pytest.raises(ProviderError, match=r"'llm' extra"):
            TenacityRetryPolicy().retrying()

    def test_retries_until_success(self) -> None:
        pytest.importorskip("tenacity")
        policy = TenacityRetryPolicy(
            max_attempts=4,
            min_backoff_seconds=0.0,
            max_backoff_seconds=0.0,
            jitter_seconds=0.0,
        )
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("503 Service Unavailable")
            return "ok"

        for attempt in policy.retrying():
            with attempt:
                result = flaky()
        assert result == "ok"
        assert calls["n"] == 3
