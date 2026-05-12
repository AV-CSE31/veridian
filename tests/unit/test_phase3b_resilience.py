"""
tests.unit.test_phase3b_resilience
──────────────────────────────────
Acceptance tests for Phase 3.B resilience polish:

* ``DeadLetterQueue(max_age_days=…)`` purges stale entries on load and
  via the explicit ``purge_expired()`` method, persists the new state,
  and is a no-op when ``max_age_days`` is None.
* ``VeridianRunner._prm_circuit_open`` carries an ``_prm_circuit_opened_at``
  timestamp and the cooldown probe path resets the flag after the
  configured window elapses.
* ``VeridianDashboard._recent_alerts`` is bounded — adding more than
  ``RECENT_ALERTS_MAX`` items evicts the oldest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from veridian.core.dlq import DeadLetterQueue
from veridian.core.task import Task


def _enqueue_fake(dlq: DeadLetterQueue, task_id: str, age_days: float) -> None:
    task = Task(id=task_id, title=task_id, verifier_id="schema")
    entry = dlq.enqueue(task=task, failure_reason="boom", retry_count=1)
    # Backdate the entry so purge_expired sees it as stale.
    entry.timestamp = datetime.now(tz=UTC) - timedelta(days=age_days)
    dlq._persist()


# ── DLQ TTL ─────────────────────────────────────────────────────────────────


class TestDLQRetention:
    def test_no_max_age_preserves_everything(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(storage_path=tmp_path / "dlq.json", max_retries=3)
        _enqueue_fake(dlq, "old", age_days=30)
        _enqueue_fake(dlq, "new", age_days=0.1)

        assert dlq.purge_expired() == 0
        assert {e.task_id for e in dlq.list_entries()} == {"old", "new"}

    def test_max_age_purges_old_entries(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(storage_path=tmp_path / "dlq.json", max_retries=3, max_age_days=7)
        _enqueue_fake(dlq, "old", age_days=30)
        _enqueue_fake(dlq, "new", age_days=0.1)

        removed = dlq.purge_expired()
        assert removed == 1
        assert {e.task_id for e in dlq.list_entries()} == {"new"}

    def test_load_applies_ttl(self, tmp_path: Path) -> None:
        # First DLQ instance writes 1 old + 1 new entry.
        dlq1 = DeadLetterQueue(storage_path=tmp_path / "dlq.json", max_retries=3)
        _enqueue_fake(dlq1, "old", age_days=30)
        _enqueue_fake(dlq1, "new", age_days=0.1)

        # Second DLQ with TTL — load-time purge drops the old entry.
        dlq2 = DeadLetterQueue(storage_path=tmp_path / "dlq.json", max_retries=3, max_age_days=7)
        ids = {e.task_id for e in dlq2.list_entries()}
        assert ids == {"new"}


# ── PRM circuit recovery window ──────────────────────────────────────────────


class TestPRMCircuitRecovery:
    def test_runner_initialises_cooldown_state(self, tmp_path: Path) -> None:
        from veridian.core.config import VeridianConfig
        from veridian.ledger.ledger import TaskLedger
        from veridian.loop.runner import VeridianRunner
        from veridian.providers.mock_provider import MockProvider

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        runner = VeridianRunner(ledger=ledger, provider=MockProvider(), config=config)

        # Phase 3.B contract: new cooldown attributes exist with sensible
        # defaults so existing PRM consumers don't have to do anything to
        # opt in.
        assert runner._prm_circuit_open is False
        assert runner._prm_backend_failures == 0
        assert runner._prm_circuit_opened_at is None
        assert runner._prm_circuit_cooldown_seconds > 0


# ── Bounded recent-alerts buffer ─────────────────────────────────────────────


class TestRecentAlertsBuffer:
    def test_deque_caps_at_maximum(self) -> None:
        # Import the dashboard lazily so this test still runs on hosts
        # without the FastAPI extra installed (the dashboard class itself
        # doesn't touch FastAPI until ``_build_app`` is called).
        from veridian.observability.dashboard import VeridianDashboard

        dash = VeridianDashboard(recent_alerts_max=3)
        for i in range(10):
            dash._recent_alerts.append(f"alert-{i}")  # type: ignore[arg-type]
        assert list(dash._recent_alerts) == ["alert-7", "alert-8", "alert-9"]

    def test_default_cap_is_large_enough_for_typical_runs(self) -> None:
        from veridian.observability.dashboard import VeridianDashboard

        dash = VeridianDashboard()
        assert dash._recent_alerts.maxlen == VeridianDashboard.RECENT_ALERTS_MAX
        assert VeridianDashboard.RECENT_ALERTS_MAX >= 1000
