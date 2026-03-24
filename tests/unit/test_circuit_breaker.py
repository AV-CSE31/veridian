"""
tests.unit.test_circuit_breaker
────────────────────────────────
Tests for the circuit breaker and retry resilience layer in LiteLLMProvider.
"""
import pytest
import time
from unittest.mock import patch, MagicMock

from veridian.providers.litellm_provider import CircuitBreaker, CBState
from veridian.core.exceptions import ProviderError, ProviderRateLimited


class TestCircuitBreaker:

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CBState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CBState.OPEN
        assert cb.allow_request() is False

    def test_resets_on_success(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CBState.CLOSED
        cb.record_success()
        assert cb._failures == 0

    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0)
        cb.record_failure()
        assert cb.state == CBState.OPEN

        # Cooldown = 0, so immediately moves to HALF_OPEN
        time.sleep(0.01)
        result = cb.allow_request()
        assert result is True
        assert cb.state == CBState.HALF_OPEN

    def test_closes_after_half_open_successes(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            cooldown_seconds=0,
            half_open_successes=2,
        )
        cb.record_failure()
        time.sleep(0.01)
        cb.allow_request()  # → HALF_OPEN
        assert cb.state == CBState.HALF_OPEN

        cb.record_success()   # 1/2
        assert cb.state == CBState.HALF_OPEN
        cb.record_success()   # 2/2 → CLOSED
        assert cb.state == CBState.CLOSED

    def test_reopens_on_half_open_failure(self):
        cb = CircuitBreaker(
            name="test",
            failure_threshold=1,
            cooldown_seconds=0,
        )
        cb.record_failure()
        time.sleep(0.01)
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()   # → OPEN again
        assert cb.state == CBState.OPEN

    def test_blocked_while_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=9999)
        cb.record_failure()
        assert cb.allow_request() is False

    def test_to_dict(self):
        cb = CircuitBreaker(name="my_model", failure_threshold=5)
        d = cb.to_dict()
        assert d["name"] == "my_model"
        assert d["state"] == "closed"
        assert d["failures"] == 0


class TestRetryableErrors:

    def test_rate_limit_is_retryable(self):
        from veridian.providers.litellm_provider import _is_retryable
        assert _is_retryable(Exception("429 rate limit exceeded")) is True

    def test_server_error_is_retryable(self):
        from veridian.providers.litellm_provider import _is_retryable
        assert _is_retryable(Exception("503 service unavailable")) is True

    def test_timeout_is_retryable(self):
        from veridian.providers.litellm_provider import _is_retryable
        assert _is_retryable(Exception("connection timeout")) is True

    def test_bad_request_is_not_retryable(self):
        from veridian.providers.litellm_provider import _is_retryable
        assert _is_retryable(Exception("400 bad request")) is False

    def test_auth_failure_is_not_retryable(self):
        from veridian.providers.litellm_provider import _is_retryable
        assert _is_retryable(Exception("401 unauthorized")) is False


class TestMockProvider:
    """Verify MockProvider behaviour used in all other tests."""

    def test_script_responses_in_order(self):
        from veridian.providers.mock_provider import MockProvider
        from veridian.providers.base import LLMResponse, Message

        mock = MockProvider()
        mock.script([
            LLMResponse(content="first"),
            LLMResponse(content="second"),
        ])
        msgs = [Message(role="user", content="hi")]
        assert mock.complete(msgs).content == "first"
        assert mock.complete(msgs).content == "second"

    def test_script_harness_result(self):
        from veridian.providers.mock_provider import MockProvider
        from veridian.providers.base import Message

        mock = MockProvider()
        mock.script_veridian_result({"status": "compliant"}, summary="ok")
        resp = mock.complete([Message(role="user", content="go")])
        assert "veridian:result" in resp.content
        assert "compliant" in resp.content

    def test_call_count(self):
        from veridian.providers.mock_provider import MockProvider
        from veridian.providers.base import Message

        mock = MockProvider()
        msgs = [Message(role="user", content="x")]
        mock.complete(msgs)
        mock.complete(msgs)
        assert mock.call_count == 2

    def test_reset(self):
        from veridian.providers.mock_provider import MockProvider
        from veridian.providers.base import Message, LLMResponse

        mock = MockProvider()
        mock.script([LLMResponse(content="x")])
        mock.complete([Message(role="user", content="y")])
        mock.reset()
        assert mock.call_count == 0
        assert mock._queue == []
