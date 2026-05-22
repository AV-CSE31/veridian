"""
veridian.providers.retry
---------------------------------------------------------------
Provider-agnostic retry policy.

Splits the "when to retry / how long to wait" decision from the provider
implementation so a single shared policy can govern every provider
(LiteLLM today, additional providers in the future) and so operators
can supply their own ``RetryPolicy`` without subclassing the provider.

The default :class:`TenacityRetryPolicy` requires the ``[llm]`` extra
(tenacity). The base :class:`RetryPolicy` lives in stdlib-only code so
the module imports cleanly without that extra installed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from veridian.core.exceptions import ProviderError

if TYPE_CHECKING:
    from tenacity import Retrying

__all__ = ["RetryPolicy", "TenacityRetryPolicy", "default_is_retryable"]


_TRANSIENT_STATUS_CODES = (429, 500, 502, 503, 504)
_PERMANENT_STATUS_CODES = ("400", "401", "403", "404")


def default_is_retryable(exc: BaseException) -> bool:
    """Heuristic shared by every provider: retry transient errors only.

    Returns True for rate limits, 5xx, timeouts, connection resets, and
    overloaded responses. Returns False for 4xx client errors that will
    keep failing on retry (auth, not found, bad request).
    """
    msg = str(exc).lower()
    if any(str(code) in msg for code in _TRANSIENT_STATUS_CODES):
        return True
    if any(kw in msg for kw in ("timeout", "connection", "network", "overloaded")):
        return True
    return not any(code in msg for code in _PERMANENT_STATUS_CODES)


@dataclass
class RetryPolicy:
    """Configuration record for a provider's retry behaviour.

    Plain dataclass so it can be instantiated, compared, and serialised
    without the optional tenacity dependency. The actual retry execution
    happens in :class:`TenacityRetryPolicy` which lazy-imports tenacity.
    """

    max_attempts: int = 4  # initial call + max_attempts-1 retries
    min_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    jitter_seconds: float = 2.0
    is_retryable: Callable[[BaseException], bool] = default_is_retryable

    def build(self) -> RetryPolicy:
        """Return the executable form of this policy.

        Concrete subclasses override; the base class returns itself so
        callers can treat it uniformly.
        """
        return self

    def retrying(self) -> Retrying:  # pragma: no cover - abstract
        raise NotImplementedError("Use TenacityRetryPolicy or a custom subclass")


class TenacityRetryPolicy(RetryPolicy):
    """Executes :class:`RetryPolicy` via the tenacity library."""

    def retrying(self) -> Retrying:
        try:
            from tenacity import (  # noqa: PLC0415
                Retrying,
                retry_if_exception,
                stop_after_attempt,
                wait_exponential_jitter,
            )
        except ImportError as exc:
            raise ProviderError(
                "TenacityRetryPolicy requires the 'llm' extra. "
                "Install with: pip install 'veridian-ai[llm]'"
            ) from exc

        return Retrying(
            retry=retry_if_exception(self.is_retryable),
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(
                initial=self.min_backoff_seconds,
                max=self.max_backoff_seconds,
                jitter=self.jitter_seconds,
            ),
            reraise=True,
        )


# Convenience: a single shared default used when callers don't supply one.
_DEFAULT_POLICY = TenacityRetryPolicy()


def _default_policy_for(
    max_retries: int,
    min_backoff: float,
    max_backoff: float,
    jitter: float,
) -> TenacityRetryPolicy:
    """Build a per-provider policy from the legacy constructor kwargs.

    Kept package-private so callers that don't customise retry just pass
    a single ``RetryPolicy`` instance, while existing providers can map
    their granular kwargs onto the policy without breaking the public
    constructor signature.
    """
    return TenacityRetryPolicy(
        max_attempts=max(1, max_retries + 1),
        min_backoff_seconds=min_backoff,
        max_backoff_seconds=max_backoff,
        jitter_seconds=jitter,
    )


def _shared_default() -> TenacityRetryPolicy:
    return _DEFAULT_POLICY
