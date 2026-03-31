"""
Tests for veridian.hooks.builtin.adaptive_safety — trust-based verification scaling.
TDD: RED phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from veridian.hooks.builtin.adaptive_safety import (
    AdaptiveSafetyHook,
    TrustLevel,
    TrustScore,
    VerificationLevel,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeRunStarted:
    run_id: str = "run-001"
    total_tasks: int = 10


@dataclass
class _FakeTask:
    id: str = "t1"
    verifier_id: str = "schema"
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class _FakeTaskCompleted:
    event_type: str = "task.completed"
    task: Any = None
    result: Any = None


@dataclass
class _FakeTaskFailed:
    event_type: str = "task.failed"
    task: Any = None
    error: str = ""


# ── TrustScore ──────────────────────────────────────────────────────────────


class TestTrustScore:
    def test_new_agent_starts_at_zero(self) -> None:
        ts = TrustScore()
        assert ts.score == 0
        assert ts.level == TrustLevel.NEW

    def test_score_increases_on_success(self) -> None:
        ts = TrustScore()
        ts.record_success()
        assert ts.score > 0

    def test_score_resets_on_safety_failure(self) -> None:
        ts = TrustScore(score=500)
        ts.record_safety_failure()
        assert ts.score == 0
        assert ts.level == TrustLevel.NEW

    def test_trust_levels(self) -> None:
        assert TrustScore(score=0).level == TrustLevel.NEW
        assert TrustScore(score=50).level == TrustLevel.LOW
        assert TrustScore(score=500).level == TrustLevel.MEDIUM
        assert TrustScore(score=1500).level == TrustLevel.HIGH

    def test_ratchet_up_is_slow(self) -> None:
        ts = TrustScore()
        for _ in range(10):
            ts.record_success()
        assert ts.score < 100  # slow ramp

    def test_ratchet_down_is_instant(self) -> None:
        ts = TrustScore(score=1000)
        ts.record_safety_failure()
        assert ts.score == 0

    def test_to_dict(self) -> None:
        ts = TrustScore(score=500)
        d = ts.to_dict()
        assert d["score"] == 500
        assert "level" in d


# ── VerificationLevel ───────────────────────────────────────────────────────


class TestVerificationLevel:
    def test_high_trust_gets_relaxed(self) -> None:
        level = VerificationLevel.for_trust(TrustLevel.HIGH)
        assert level == VerificationLevel.RELAXED

    def test_medium_trust_gets_standard(self) -> None:
        level = VerificationLevel.for_trust(TrustLevel.MEDIUM)
        assert level == VerificationLevel.STANDARD

    def test_low_trust_gets_strict(self) -> None:
        level = VerificationLevel.for_trust(TrustLevel.LOW)
        assert level == VerificationLevel.STRICT

    def test_new_trust_gets_maximum(self) -> None:
        level = VerificationLevel.for_trust(TrustLevel.NEW)
        assert level == VerificationLevel.MAXIMUM


# ── AdaptiveSafetyHook ──────────────────────────────────────────────────────


class TestAdaptiveSafetyHook:
    def test_creates_with_defaults(self) -> None:
        hook = AdaptiveSafetyHook()
        assert hook.id == "adaptive_safety"

    def test_returns_verification_level_for_task(self) -> None:
        hook = AdaptiveSafetyHook()
        hook.before_run(_FakeRunStarted())
        level = hook.get_verification_level()
        assert level == VerificationLevel.MAXIMUM  # starts at NEW

    def test_trust_increases_after_successes(self) -> None:
        hook = AdaptiveSafetyHook()
        hook.before_run(_FakeRunStarted())
        for i in range(20):
            hook.after_task(_FakeTaskCompleted(task=_FakeTask(id=f"t{i}")))
        assert hook._trust.score > 0

    def test_trust_resets_on_safety_failure(self) -> None:
        hook = AdaptiveSafetyHook()
        hook.before_run(_FakeRunStarted())
        for i in range(20):
            hook.after_task(_FakeTaskCompleted(task=_FakeTask(id=f"t{i}")))
        initial_score = hook._trust.score
        assert initial_score > 0
        hook.on_failure(_FakeTaskFailed(
            task=_FakeTask(id="bad", metadata={"safety_violation": True}),
            error="safety violation",
        ))
        assert hook._trust.score == 0

    def test_current_level_exposed(self) -> None:
        hook = AdaptiveSafetyHook()
        hook.before_run(_FakeRunStarted())
        assert hook.get_verification_level() in (
            VerificationLevel.RELAXED,
            VerificationLevel.STANDARD,
            VerificationLevel.STRICT,
            VerificationLevel.MAXIMUM,
        )
