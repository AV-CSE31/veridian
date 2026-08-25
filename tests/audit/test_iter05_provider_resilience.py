"""
ADVERSARIAL AUDIT — Iteration 5: LiteLLMProvider resilience.

The circuit breaker / retry / fallback ARE wired (not cosmetic) — noted, moving
on. The defects are about what the stack does to wall-clock and cost under the
exact failure the founder was burned by (hung Gemini calls).

  I5-1 (P1): no global deadline. Per-attempt timeout × (max_retries+1) × N models
             means a hanging endpoint chain is hammered 12x (default), turning a
             20-min pain into a ~24-min pain. The resilience stack AMPLIFIES the
             hang it claims to defend against.
  I5-2 (P1): _is_retryable classifies by substring of the error string, and the
             default is "retry on unknown". A deterministic bug (ValueError with
             no status code) is retried max_retries+1 times — paying for the same
             guaranteed failure 4x.
"""

from __future__ import annotations

import sys
import types

import pytest

from veridian.core.exceptions import ProviderError
from veridian.providers.base import Message
from veridian.providers.litellm_provider import LiteLLMProvider


def _inject_litellm(monkeypatch, raiser) -> dict:
    calls = {"n": 0}
    fake = types.ModuleType("litellm")

    def completion(**kwargs):
        calls["n"] += 1
        raise raiser()

    fake.completion = completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


def test_I5_1_hanging_chain_has_a_global_deadline(monkeypatch) -> None:
    """A caller who sets max_retries=3 expects ~4 attempts, not 4 x (1+fallbacks).
    With a per-attempt-only timeout, a fully-hanging chain is retried far past any
    budget the caller could reason about. There must be a global cap.
    """
    calls = _inject_litellm(monkeypatch, lambda: TimeoutError("Request timed out after 120s"))
    provider = LiteLLMProvider(
        model="gemini/gemini-2.5-flash",
        max_retries=3,
        min_backoff=0.0,
        max_backoff=0.0,
        jitter=0.0,
        fallback_models=["gemini/gemini-2.0-flash", "gpt-4o-mini"],
    )
    with pytest.raises(ProviderError):
        provider.complete([Message(role="user", content="hi")])

    assert calls["n"] <= provider.max_retries + 1, (
        f"A hanging endpoint chain was called {calls['n']} times for a single "
        f"complete() (max_retries+1 = {provider.max_retries + 1}). Each call "
        "carries the full per-attempt timeout and there is no global deadline, so "
        "retries x fallbacks multiply the very hang the resilience stack advertises "
        "it prevents. The founder's 20-min Gemini timeout becomes ~24 min here."
    )


def test_I5_2_deterministic_bug_is_not_retried(monkeypatch) -> None:
    """A ValueError with no status code is a deterministic failure (a bug or a
    malformed response), not a transient API error. Retrying it pays for the same
    guaranteed failure N times. _is_retryable's 'retry on unknown' default does
    exactly that.
    """
    calls = _inject_litellm(monkeypatch, lambda: ValueError("unexpected None in choices[0]"))
    provider = LiteLLMProvider(
        model="gemini/gemini-2.5-flash",
        max_retries=3,
        min_backoff=0.0,
        max_backoff=0.0,
        jitter=0.0,
    )
    with pytest.raises(ProviderError):
        provider.complete([Message(role="user", content="hi")])

    assert calls["n"] == 1, (
        f"A non-API ValueError was retried {calls['n']} times. _is_retryable "
        "defaults to 'retry on unknown error', so deterministic bugs and malformed "
        "responses are treated as transient — paying real API cost and latency for "
        "a failure that will never succeed."
    )


def test_retry_stack_reports_missing_tenacity_extra(monkeypatch) -> None:
    """Retry remains a lazy optional dependency with an actionable failure."""
    monkeypatch.setitem(sys.modules, "tenacity", None)
    provider = LiteLLMProvider(model="gemini/gemini-2.5-flash")

    with pytest.raises(ProviderError, match=r"veridian-ai\[llm\]"):
        provider._complete_with_retry(
            provider.model,
            [Message(role="user", content="hi")],
        )
