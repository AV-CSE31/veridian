"""
veridian.providers.litellm_provider
────────────────────────────────────
Production-grade LiteLLM provider with:

  ┌─────────────────────────────────────────────────────────┐
  │  RESILIENCE STACK (outermost → innermost)                │
  │                                                          │
  │  1. CircuitBreaker  — stops hammering a dead endpoint    │
  │     CLOSED → OPEN after N failures → HALF_OPEN probe    │
  │                                                          │
  │  2. Retry w/ exponential backoff + jitter (tenacity)     │
  │     Retries transient errors: 429, 503, timeout         │
  │     Fails fast on permanent errors: 400, 401, 404       │
  │                                                          │
  │  3. Fallback model chain                                 │
  │     primary → fallback[0] → fallback[1] → ...           │
  │     Only activates after primary exhausts retries        │
  │                                                          │
  │  4. Context window guard                                 │
  │     Raises ContextWindowExceeded before the API call    │
  └─────────────────────────────────────────────────────────┘

Circuit breaker states:
  CLOSED   → normal operation, all requests pass through
  OPEN     → endpoint down, fail immediately for cooldown_seconds
  HALF_OPEN → send one probe; close on success, re-open on failure
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from veridian.core.exceptions import (
    ContextWindowExceeded,
    ProviderError,
    ProviderRateLimited,
)
from veridian.providers.base import LLMProvider, LLMResponse, Message

log = logging.getLogger(__name__)


# ── CIRCUIT BREAKER ───────────────────────────────────────────────────────────

class CBState(StrEnum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Thread-safe circuit breaker for a single LLM endpoint.

    failure_threshold  : consecutive failures before OPEN
    cooldown_seconds   : seconds to wait in OPEN before probing
    half_open_successes: consecutive successes in HALF_OPEN before CLOSED
    """
    name: str = "default"
    failure_threshold: int = 5
    cooldown_seconds: int  = 60
    half_open_successes: int = 2

    _state: CBState = field(default=CBState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _half_open_ok: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> CBState:
        return self._state

    def allow_request(self) -> bool:
        """
        Returns True if request should proceed.
        Transitions OPEN → HALF_OPEN when cooldown expires.
        """
        with self._lock:
            if self._state == CBState.CLOSED:
                return True
            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed >= self.cooldown_seconds:
                    log.info("circuit_breaker.half_open name=%s", self.name)
                    self._state = CBState.HALF_OPEN
                    self._half_open_ok = 0
                    return True   # allow one probe
                return False
            # HALF_OPEN: allow probes through
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state == CBState.HALF_OPEN:
                self._half_open_ok += 1
                if self._half_open_ok >= self.half_open_successes:
                    log.info("circuit_breaker.closed name=%s", self.name)
                    self._state = CBState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == CBState.HALF_OPEN:
                # Probe failed — re-open immediately
                log.warning("circuit_breaker.reopen name=%s", self.name)
                self._state = CBState.OPEN
                self._opened_at = time.monotonic()
            elif self._failures >= self.failure_threshold:
                if self._state != CBState.OPEN:
                    log.warning(
                        "circuit_breaker.open name=%s failures=%d",
                        self.name, self._failures,
                    )
                    self._state = CBState.OPEN
                    self._opened_at = time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._failures,
            "cooldown_seconds": self.cooldown_seconds,
        }


# ── RETRY POLICY ─────────────────────────────────────────────────────────────

# Errors worth retrying (transient)
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

def _is_retryable(exc: BaseException) -> bool:
    """
    True → tenacity will retry.
    False → fail immediately (permanent error).
    """
    msg = str(exc).lower()
    # Rate limits / server errors
    for code in _TRANSIENT_STATUS_CODES:
        if str(code) in msg:
            return True
    # Connection / timeout errors
    if any(kw in msg for kw in ("timeout", "connection", "network", "overloaded")):
        return True
    # Permanent: bad request, auth failure, not found — default: retry on unknown errors
    return not any(code in msg for code in ("400", "401", "403", "404"))


# ── LITELLM PROVIDER ─────────────────────────────────────────────────────────

class LiteLLMProvider(LLMProvider):
    """
    Default provider. Supports 100+ models via LiteLLM.

    Model is selected by:
      1. constructor `model=` argument
      2. VERIDIAN_MODEL env var
      3. Default: "gemini/gemini-2.5-flash"

    Resilience features (all configurable):
      - Circuit breaker per model endpoint
      - Exponential backoff with jitter via tenacity
      - Fallback model chain
      - Context window budget guard
      - Retry-After header respect (via LiteLLM)
    """

    DEFAULT_MODEL = "gemini/gemini-2.5-flash"

    # Approximate context limits per model family (tokens)
    CONTEXT_LIMITS: dict[str, int] = {
        "gemini/gemini-2.5-flash":   1_048_576,
        "gemini/gemini-2.0-flash":   1_048_576,
        "claude-opus-4-6":             200_000,
        "claude-sonnet-4-6":           200_000,
        "gpt-4o":                      128_000,
        "gpt-4o-mini":                 128_000,
        "default":                     100_000,
    }

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: int = 120,
        # Retry
        max_retries: int = 3,
        min_backoff: float = 1.0,
        max_backoff: float = 30.0,
        jitter: float = 2.0,
        # Circuit breaker
        cb_failure_threshold: int = 5,
        cb_cooldown_seconds: int = 60,
        # Fallback
        fallback_models: list[str] | None = None,
        # Context guard
        context_window_budget: int | None = None,
    ) -> None:
        self.model: str = model or os.getenv("VERIDIAN_MODEL") or self.DEFAULT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_backoff = min_backoff
        self.max_backoff = max_backoff
        self.jitter = jitter
        self.fallback_models = fallback_models or []
        self.context_window_budget = context_window_budget or self._model_context_limit()

        # One circuit breaker per model in the chain
        all_models = [self.model] + self.fallback_models
        self._circuit_breakers: dict[str, CircuitBreaker] = {
            m: CircuitBreaker(
                name=m,
                failure_threshold=cb_failure_threshold,
                cooldown_seconds=cb_cooldown_seconds,
            )
            for m in all_models
        }

        # Lazy import — litellm is optional at import time
        self._litellm: Any = None

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        """
        Synchronous completion with full resilience stack.
        Tries primary model, then fallbacks on exhausted retries.
        """
        models_to_try = [self.model] + self.fallback_models
        last_exc: Exception | None = None

        for model in models_to_try:
            cb = self._circuit_breakers.get(model)
            if cb and not cb.allow_request():
                log.warning("circuit_breaker.blocked model=%s", model)
                last_exc = ProviderRateLimited(
                    f"Circuit breaker OPEN for {model}. "
                    f"Cooldown: {cb.cooldown_seconds}s"
                )
                continue

            try:
                response = self._complete_with_retry(model, messages, **kwargs)
                if cb:
                    cb.record_success()
                return response
            except Exception as exc:
                last_exc = exc
                if cb:
                    cb.record_failure()
                log.warning(
                    "provider.complete failed model=%s err=%s trying_fallback=%s",
                    model, exc, bool(self.fallback_models),
                )
                # Only try fallback if primary exhausted all retries
                continue

        raise ProviderError(
            f"All models failed. Last error: {last_exc}"
        ) from last_exc

    async def complete_async(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        """Async wrapper — runs sync complete() in executor to avoid blocking."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.complete, messages)

    def _complete_with_retry(
        self, model: str, messages: list[Message], **kwargs: Any
    ) -> LLMResponse:
        """
        Call LiteLLM with tenacity retry (exponential backoff + jitter).
        Fails immediately on permanent errors (4xx non-429).
        """
        import litellm  # noqa: PLC0415

        # Guard: check context window before making the API call
        estimated_tokens = self.count_tokens(" ".join(m.content for m in messages))
        if estimated_tokens > self.context_window_budget:
            raise ContextWindowExceeded(
                f"Estimated {estimated_tokens} tokens exceeds budget "
                f"{self.context_window_budget} for model {model}. "
                f"Compact context or reduce task scope."
            )

        lm_messages = [{"role": m.role, "content": m.content} for m in messages]

        def _call() -> LLMResponse:
            resp = litellm.completion(
                model=model,
                messages=lm_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
                **kwargs,
            )
            usage = resp.usage or {}
            content = resp.choices[0].message.content or ""
            return LLMResponse(
                content=content,
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0),
                model=model,
                finish_reason=resp.choices[0].finish_reason or "",
            )

        try:
            for attempt in Retrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(self.max_retries + 1),
                wait=wait_exponential_jitter(
                    initial=self.min_backoff,
                    max=self.max_backoff,
                    jitter=self.jitter,
                ),
                reraise=True,
            ):
                with attempt:
                    log.debug(
                        "provider.call model=%s attempt=%d",
                        model, attempt.retry_state.attempt_number,
                    )
                    return _call()
        except RetryError as e:
            raise ProviderError(f"Retries exhausted for {model}: {e}") from e
        raise ProviderError(f"No attempts executed for {model}")

    def count_tokens(self, text: str) -> int:
        """Use tiktoken if available, else character approximation."""
        try:
            import tiktoken  # noqa: PLC0415
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def _model_context_limit(self) -> int:
        for prefix, limit in self.CONTEXT_LIMITS.items():
            if self.model.startswith(prefix):
                return limit
        return self.CONTEXT_LIMITS["default"]

    def circuit_breaker_status(self) -> list[dict[str, Any]]:
        """Returns current state of all circuit breakers. For monitoring."""
        return [cb.to_dict() for cb in self._circuit_breakers.values()]
