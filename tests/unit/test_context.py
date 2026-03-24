"""
tests.unit.test_context
────────────────────────
Unit tests for TokenWindow, ContextCompactor, and ContextManager.
"""
import pytest

from veridian.context.window import TokenWindow
from veridian.context.compactor import ContextCompactor
from veridian.context.manager import ContextManager
from veridian.core.task import Task, TaskStatus


# ── TokenWindow ───────────────────────────────────────────────────────────────

class TestTokenWindow:

    def test_fits_within_capacity(self):
        w = TokenWindow(capacity=1000)
        assert w.fits(500) is True

    def test_does_not_fit_over_capacity(self):
        w = TokenWindow(capacity=100)
        assert w.fits(200) is False

    def test_consume_reduces_remaining(self):
        w = TokenWindow(capacity=1000)
        w.consume(300)
        assert w.remaining == 700
        assert w.used == 300

    def test_pct_used(self):
        w = TokenWindow(capacity=1000)
        w.consume(500)
        assert w.pct_used == pytest.approx(0.5)

    def test_reset_clears_used(self):
        w = TokenWindow(capacity=1000)
        w.consume(500)
        w.reset()
        assert w.used == 0
        assert w.remaining == 1000

    def test_fits_exact_boundary(self):
        w = TokenWindow(capacity=100)
        assert w.fits(100) is True
        w.consume(100)
        assert w.fits(1) is False

    def test_zero_capacity_raises(self):
        with pytest.raises(ValueError):
            TokenWindow(capacity=0)


# ── ContextCompactor ──────────────────────────────────────────────────────────

class TestContextCompactor:

    def test_needs_compaction_at_85_pct(self):
        w = TokenWindow(capacity=1000)
        c = ContextCompactor(w)
        w.consume(850)
        assert c.needs_compaction() is True

    def test_no_compaction_below_85_pct(self):
        w = TokenWindow(capacity=1000)
        c = ContextCompactor(w)
        w.consume(840)
        assert c.needs_compaction() is False

    def test_compact_preserves_system_and_tail(self):
        w = TokenWindow(capacity=10000)
        c = ContextCompactor(w)
        messages = [
            {"role": "system", "content": "you are an agent"},
            {"role": "user", "content": "task block"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "resp3"},
            {"role": "user", "content": "msg4"},
            {"role": "assistant", "content": "resp4"},
        ]
        compacted = c.compact(messages)
        # System prompt must be preserved
        assert any(m["role"] == "system" for m in compacted)
        # Last exchanges must be preserved
        assert compacted[-1]["content"] == "resp4"
        # Middle messages should be dropped
        assert len(compacted) < len(messages)

    def test_compact_short_list_unchanged(self):
        w = TokenWindow(capacity=10000)
        c = ContextCompactor(w)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        result = c.compact(messages)
        assert len(result) == len(messages)


# ── ContextManager ────────────────────────────────────────────────────────────

class TestContextManager:

    @pytest.fixture
    def manager(self) -> ContextManager:
        return ContextManager(window=TokenWindow(capacity=8000))

    @pytest.fixture
    def task(self) -> Task:
        return Task(
            id="t1",
            title="Test task",
            description="Do the thing",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary", "status"]},
        )

    def test_build_worker_context_returns_messages(self, manager, task):
        """build_worker_context must return a non-empty list of message dicts."""
        messages = manager.build_worker_context(task, run_id="r1")
        assert isinstance(messages, list)
        assert len(messages) >= 1

    def test_first_block_is_system(self, manager, task):
        """Block 1 must always be the system prompt."""
        messages = manager.build_worker_context(task, run_id="r1")
        assert messages[0]["role"] == "system"

    def test_task_title_appears_in_context(self, manager, task):
        """Task title must appear in the assembled context."""
        messages = manager.build_worker_context(task, run_id="r1")
        full_text = " ".join(m["content"] for m in messages)
        assert "Test task" in full_text

    def test_retry_error_block_included_on_attempt_gt_0(self, manager, task):
        """[RETRY ERROR] block appears in user message when attempt > 0."""
        task.last_error = "schema field missing"
        messages = manager.build_worker_context(task, run_id="r1", attempt=1)
        user_text = " ".join(
            m["content"] for m in messages if m.get("role") == "user"
        )
        assert "schema field missing" in user_text
        assert "[RETRY ERROR]" in user_text

    def test_retry_error_block_excluded_on_first_attempt(self, manager, task):
        """[RETRY ERROR] block must NOT appear in user message on attempt=0."""
        task.last_error = "previous error"
        messages = manager.build_worker_context(task, run_id="r1", attempt=0)
        # Only check user messages — the system prompt may mention RETRY ERROR
        # as instructions but the actual block marker only appears on retry
        user_text = " ".join(
            m["content"] for m in messages if m.get("role") == "user"
        )
        assert "[RETRY ERROR]" not in user_text

    def test_output_format_block_always_included(self, manager, task):
        """[OUTPUT FMT] block with veridian:result must always appear."""
        messages = manager.build_worker_context(task, run_id="r1")
        full_text = " ".join(m["content"] for m in messages)
        assert "veridian:result" in full_text

    def test_error_truncated_to_300_chars(self, manager, task):
        """last_error is capped at 300 chars in context."""
        task.last_error = "x" * 500
        messages = manager.build_worker_context(task, run_id="r1", attempt=1)
        full_text = " ".join(m["content"] for m in messages)
        # Should contain 300 x's but not 500
        assert "x" * 300 in full_text
        assert "x" * 301 not in full_text

    def test_verifier_id_in_task_block(self, manager, task):
        messages = manager.build_worker_context(task, run_id="r1")
        full_text = " ".join(m["content"] for m in messages)
        assert "schema" in full_text
