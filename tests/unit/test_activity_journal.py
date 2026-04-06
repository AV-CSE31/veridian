"""
tests.unit.test_activity_journal
─────────────────────────────────
RV3-004 + RV3-005: run_activity() side-effect boundary and deterministic
retry/activity journal.

Tests the Temporal-inspired activity primitive:
- Every side effect has an activity_id + idempotency_key.
- Journal is append-only; replays return cached outputs.
- Retries are bounded, deterministic, and replay-safe.
- Retry attempts are journaled for audit.
"""

from __future__ import annotations

import pytest

from veridian.loop.activity import (
    ActivityError,
    ActivityJournal,
    ActivityRecord,
    RetryPolicy,
    run_activity,
)


class TestActivityJournal:
    def test_empty_journal_is_empty(self) -> None:
        j = ActivityJournal()
        assert len(j) == 0
        assert j.get("anything") is None

    def test_append_and_retrieve_by_idempotency_key(self) -> None:
        j = ActivityJournal()
        rec = ActivityRecord(
            activity_id="act_1",
            idempotency_key="key_a",
            fn_name="provider.complete",
            args_hash="abc",
            result={"content": "hi"},
            attempts=1,
            status="success",
            timestamp_ms=1,
        )
        j.append(rec)
        assert len(j) == 1
        fetched = j.get("key_a")
        assert fetched is not None
        assert fetched.result == {"content": "hi"}

    def test_journal_round_trips_dict(self) -> None:
        j = ActivityJournal()
        j.append(
            ActivityRecord(
                activity_id="act_1",
                idempotency_key="k",
                fn_name="fn",
                args_hash="h",
                result="ok",
                attempts=2,
                status="success",
                timestamp_ms=10,
            )
        )
        data = j.to_list()
        restored = ActivityJournal.from_list(data)
        assert len(restored) == 1
        assert restored.get("k") is not None
        assert restored.get("k").attempts == 2

    def test_duplicate_idempotency_key_overwrites_deterministically(self) -> None:
        """If the same idempotency_key appears twice, the latest wins.
        This mirrors Temporal's workflow-history semantics where a retry that
        completes successfully supersedes an earlier pending entry."""
        j = ActivityJournal()
        j.append(ActivityRecord("a1", "k", "fn", "h", None, 1, "pending", 1))
        j.append(ActivityRecord("a2", "k", "fn", "h", "ok", 2, "success", 2))
        fetched = j.get("k")
        assert fetched is not None
        assert fetched.status == "success"
        assert fetched.result == "ok"


class TestRunActivitySuccessPath:
    def test_first_call_executes_and_records(self) -> None:
        j = ActivityJournal()
        calls: list[int] = []

        def side_effect(x: int) -> int:
            calls.append(x)
            return x * 2

        result = run_activity(
            journal=j,
            fn=side_effect,
            args=(3,),
            fn_name="double",
            idempotency_key="k1",
        )
        assert result == 6
        assert calls == [3]
        record = j.get("k1")
        assert record is not None
        assert record.result == 6
        assert record.status == "success"
        assert record.attempts == 1

    def test_second_call_with_same_key_returns_cached_without_executing(
        self,
    ) -> None:
        j = ActivityJournal()
        calls: list[int] = []

        def side_effect(x: int) -> int:
            calls.append(x)
            return x * 2

        run_activity(
            journal=j,
            fn=side_effect,
            args=(3,),
            fn_name="double",
            idempotency_key="k1",
        )
        # Second call with the same key — MUST NOT re-execute
        result = run_activity(
            journal=j,
            fn=side_effect,
            args=(3,),
            fn_name="double",
            idempotency_key="k1",
        )
        assert result == 6
        assert len(calls) == 1, "Cached replay must not re-execute the function"

    def test_different_key_executes_separately(self) -> None:
        j = ActivityJournal()
        calls: list[str] = []

        def fn(label: str) -> str:
            calls.append(label)
            return label.upper()

        run_activity(journal=j, fn=fn, args=("a",), fn_name="fn", idempotency_key="k_a")
        run_activity(journal=j, fn=fn, args=("b",), fn_name="fn", idempotency_key="k_b")
        assert calls == ["a", "b"]
        assert len(j) == 2


class TestRunActivityRetry:
    def test_retries_on_exception_until_success(self) -> None:
        j = ActivityJournal()
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError(f"attempt {attempts['n']} failed")
            return "ok"

        result = run_activity(
            journal=j,
            fn=flaky,
            args=(),
            fn_name="flaky",
            idempotency_key="k",
            retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=0.0),
        )
        assert result == "ok"
        assert attempts["n"] == 3
        record = j.get("k")
        assert record is not None
        assert record.attempts == 3
        assert record.status == "success"

    def test_exhausts_retries_and_raises_activity_error(self) -> None:
        j = ActivityJournal()

        def always_fail() -> None:
            raise RuntimeError("boom")

        with pytest.raises(ActivityError) as exc_info:
            run_activity(
                journal=j,
                fn=always_fail,
                args=(),
                fn_name="always_fail",
                idempotency_key="k",
                retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0.0),
            )
        assert "3 attempts" in str(exc_info.value)
        record = j.get("k")
        assert record is not None
        assert record.attempts == 3
        assert record.status == "failed"

    def test_failed_activity_is_not_replayed_as_success(self) -> None:
        """A prior failed activity is re-run (policy may differ) — replay
        cache should never hide a failure."""
        j = ActivityJournal()
        j.append(
            ActivityRecord(
                activity_id="a1",
                idempotency_key="k",
                fn_name="fn",
                args_hash="h",
                result=None,
                attempts=3,
                status="failed",
                timestamp_ms=1,
            )
        )
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            return "ok"

        result = run_activity(
            journal=j,
            fn=fn,
            args=(),
            fn_name="fn",
            idempotency_key="k",
        )
        assert result == "ok"
        assert calls["n"] == 1


class TestRunActivityIdempotencyKeyDerivation:
    def test_auto_derives_key_from_fn_name_and_args_when_not_provided(self) -> None:
        """If idempotency_key is None, one is derived deterministically from
        fn_name + args_hash so the caller gets replay safety for free."""
        j = ActivityJournal()
        calls = {"n": 0}

        def fn(x: int) -> int:
            calls["n"] += 1
            return x

        run_activity(journal=j, fn=fn, args=(5,), fn_name="myfn")
        run_activity(journal=j, fn=fn, args=(5,), fn_name="myfn")
        assert calls["n"] == 1  # second call hit cache via derived key

    def test_derived_key_differs_for_different_args(self) -> None:
        j = ActivityJournal()
        calls: list[int] = []

        def fn(x: int) -> int:
            calls.append(x)
            return x

        run_activity(journal=j, fn=fn, args=(1,), fn_name="myfn")
        run_activity(journal=j, fn=fn, args=(2,), fn_name="myfn")
        assert calls == [1, 2]


class TestRetryPolicyValidation:
    def test_rejects_negative_max_attempts(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_rejects_negative_backoff(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=3, backoff_seconds=-1.0)

    def test_default_retry_policy(self) -> None:
        p = RetryPolicy()
        assert p.max_attempts >= 1
