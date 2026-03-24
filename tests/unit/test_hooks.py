"""
tests.unit.test_hooks
──────────────────────
Unit tests for BaseHook ABC, HookRegistry, and builtin hooks.
"""
import pytest

from veridian.core.events import RunStarted, TaskClaimed, TaskCompleted, TaskFailed
from veridian.core.exceptions import CostLimitExceeded, HumanReviewRequired
from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry


# ── BaseHook ──────────────────────────────────────────────────────────────────

class TestBaseHook:

    def test_all_methods_are_no_ops(self):
        """All lifecycle methods on BaseHook must silently no-op."""
        class MinimalHook(BaseHook):
            id = "minimal"

        hook = MinimalHook()
        event = TaskClaimed(run_id="r1")
        hook.before_task(event)
        hook.after_task(event)
        hook.before_run(event)
        hook.after_run(event)
        hook.on_failure(event)

    def test_priority_is_class_level(self):
        """Priority is a ClassVar — set on the class, not the instance."""
        class MyHook(BaseHook):
            id = "myhook"
            priority = 10

        assert MyHook.priority == 10
        hook = MyHook()
        assert hook.priority == 10

    def test_default_priority_is_50(self):
        """Default priority value is 50."""
        class DefaultHook(BaseHook):
            id = "default_hook"

        assert DefaultHook.priority == 50


# ── HookRegistry ──────────────────────────────────────────────────────────────

class TestHookRegistry:

    def test_fire_calls_hooks_in_ascending_priority_order(self):
        """Lower priority number runs first."""
        calls: list[str] = []

        class HookA(BaseHook):
            id = "a"
            priority = 50
            def before_task(self, event: object) -> None:
                calls.append("a")

        class HookB(BaseHook):
            id = "b"
            priority = 10
            def before_task(self, event: object) -> None:
                calls.append("b")

        reg = HookRegistry()
        reg.register(HookA())
        reg.register(HookB())
        reg.fire("before_task", TaskClaimed(run_id="r1"))
        assert calls == ["b", "a"]

    def test_broken_hook_never_kills_run(self):
        """Hook exceptions must be swallowed by HookRegistry. Run continues."""
        class BrokenHook(BaseHook):
            id = "broken"
            def before_task(self, event: object) -> None:
                raise RuntimeError("hook exploded")

        reg = HookRegistry()
        reg.register(BrokenHook())
        # Must NOT raise — the run must continue
        reg.fire("before_task", TaskClaimed(run_id="r1"))

    def test_register_and_list(self):
        """Registered hooks are accessible via the hooks property."""
        class H(BaseHook):
            id = "h1"

        reg = HookRegistry()
        reg.register(H())
        assert len(reg.hooks) == 1

    def test_fire_no_hooks_is_no_op(self):
        """Firing on an empty registry does not raise."""
        reg = HookRegistry()
        reg.fire("before_task", TaskClaimed(run_id="r1"))

    def test_fire_missing_method_is_no_op(self):
        """If a hook does not implement the method, skip it silently."""
        class SelectiveHook(BaseHook):
            id = "selective"
            # Only implements before_run, not before_task

        reg = HookRegistry()
        reg.register(SelectiveHook())
        reg.fire("before_task", TaskClaimed(run_id="r1"))  # should not raise

    def test_multiple_hooks_fire_all(self):
        """All registered hooks receive the event."""
        counts: list[int] = [0]

        class Counter(BaseHook):
            id = "counter"
            def before_run(self, event: object) -> None:
                counts[0] += 1

        reg = HookRegistry()
        reg.register(Counter())
        reg.register(Counter())
        reg.fire("before_run", RunStarted(run_id="r1"))
        assert counts[0] == 2

    def test_hook_isolation_test(self):
        """Mandatory hook isolation: BrokenHook cannot propagate exceptions."""
        class BrokenHook(BaseHook):
            id = "broken"
            def before_task(self, event: object) -> None:
                raise RuntimeError("hook exploded")

        registry = HookRegistry()
        registry.register(BrokenHook())
        registry.fire("before_task", TaskClaimed(run_id="t1"))  # must not raise


# ── LoggingHook ───────────────────────────────────────────────────────────────

class TestLoggingHook:

    def test_priority_is_zero(self):
        from veridian.hooks.builtin.logging_hook import LoggingHook
        assert LoggingHook.priority == 0

    def test_before_task_does_not_raise(self):
        from veridian.hooks.builtin.logging_hook import LoggingHook
        hook = LoggingHook()
        task = Task(title="test", id="t1")
        hook.before_task(TaskClaimed(run_id="r1", task=task))

    def test_after_task_does_not_raise(self):
        from veridian.hooks.builtin.logging_hook import LoggingHook
        hook = LoggingHook()
        task = Task(title="test", id="t1", status=TaskStatus.DONE)
        hook.after_task(TaskCompleted(run_id="r1", task=task))

    def test_on_failure_does_not_raise(self):
        from veridian.hooks.builtin.logging_hook import LoggingHook
        hook = LoggingHook()
        task = Task(title="test", id="t1")
        hook.on_failure(TaskFailed(run_id="r1", task=task, error="boom"))


# ── CostGuardHook ─────────────────────────────────────────────────────────────

class TestCostGuardHook:

    def test_no_raise_under_budget(self):
        from veridian.hooks.builtin.cost_guard import CostGuardHook
        hook = CostGuardHook(max_cost_usd=10.0)
        hook.before_task(TaskClaimed(run_id="r1"))  # must not raise

    def test_raises_cost_limit_exceeded_when_over_budget(self):
        from veridian.hooks.builtin.cost_guard import CostGuardHook
        hook = CostGuardHook(max_cost_usd=0.01)
        hook._current_cost = 0.02
        with pytest.raises(CostLimitExceeded):
            hook.before_task(TaskClaimed(run_id="r1"))

    def test_accumulates_cost_from_task_tokens(self):
        from veridian.hooks.builtin.cost_guard import CostGuardHook
        hook = CostGuardHook(max_cost_usd=100.0, cost_per_token=0.001)
        task = Task(title="t1")
        task.result = TaskResult(
            raw_output="done",
            token_usage={"total_tokens": 100},
        )
        event = TaskCompleted(run_id="r1", task=task)
        hook.after_task(event)
        assert hook.current_cost == pytest.approx(0.1)

    def test_error_message_actionable(self):
        from veridian.hooks.builtin.cost_guard import CostGuardHook
        hook = CostGuardHook(max_cost_usd=1.0)
        hook._current_cost = 2.0
        with pytest.raises(CostLimitExceeded) as exc_info:
            hook.before_task(TaskClaimed(run_id="r1"))
        assert "2" in str(exc_info.value)  # current cost visible
        assert "1" in str(exc_info.value)  # limit visible


# ── HumanReviewHook ───────────────────────────────────────────────────────────

class TestHumanReviewHook:

    def test_raises_human_review_required_when_flagged(self):
        from veridian.hooks.builtin.human_review import HumanReviewHook
        hook = HumanReviewHook()
        task = Task(title="t1", metadata={"requires_human_review": True})
        with pytest.raises(HumanReviewRequired):
            hook.before_task(TaskClaimed(run_id="r1", task=task))

    def test_no_raise_when_not_flagged(self):
        from veridian.hooks.builtin.human_review import HumanReviewHook
        hook = HumanReviewHook()
        task = Task(title="t1", metadata={})
        hook.before_task(TaskClaimed(run_id="r1", task=task))  # must not raise

    def test_no_raise_without_task(self):
        from veridian.hooks.builtin.human_review import HumanReviewHook
        hook = HumanReviewHook()
        hook.before_task(TaskClaimed(run_id="r1"))  # event with no task attached


# ── RateLimitHook ─────────────────────────────────────────────────────────────

class TestRateLimitHook:

    def test_does_not_raise_under_limit(self):
        from veridian.hooks.builtin.rate_limit import RateLimitHook
        hook = RateLimitHook(max_per_minute=100)
        hook.before_task(TaskClaimed(run_id="r1"))  # must not raise

    def test_priority_is_50(self):
        from veridian.hooks.builtin.rate_limit import RateLimitHook
        assert RateLimitHook.priority == 50


# ── SlackNotifyHook ───────────────────────────────────────────────────────────

class TestSlackNotifyHook:

    def test_no_op_when_no_webhook(self):
        from veridian.hooks.builtin.slack import SlackNotifyHook
        hook = SlackNotifyHook(webhook_url=None)
        hook.before_run(RunStarted(run_id="r1"))  # must not raise

    def test_after_run_no_op_when_no_webhook(self):
        from veridian.hooks.builtin.slack import SlackNotifyHook
        hook = SlackNotifyHook(webhook_url=None)
        hook.after_run(RunStarted(run_id="r1"))

    def test_priority_is_50(self):
        from veridian.hooks.builtin.slack import SlackNotifyHook
        assert SlackNotifyHook.priority == 50
