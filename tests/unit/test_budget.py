"""
tests/unit/test_budget.py
──────────────────────────
Tests for A7: Budget primitives.
"""

from __future__ import annotations

import time

import pytest

from veridian.budget import Budget, BudgetState
from veridian.core.exceptions import BudgetExceeded


class TestBudgetCreation:
    def test_no_limits(self) -> None:
        b = Budget()
        assert b.token_limit is None
        assert b.cost_limit_usd is None
        assert b.wall_clock_limit_seconds is None

    def test_token_limit_only(self) -> None:
        b = Budget(token_limit=10000)
        assert b.token_limit == 10000

    def test_cost_limit_only(self) -> None:
        b = Budget(cost_limit_usd=1.00)
        assert b.cost_limit_usd == 1.00

    def test_wall_clock_limit_only(self) -> None:
        b = Budget(wall_clock_limit_seconds=300.0)
        assert b.wall_clock_limit_seconds == 300.0

    def test_all_limits(self) -> None:
        b = Budget(token_limit=5000, cost_limit_usd=0.50, wall_clock_limit_seconds=120.0)
        assert b.token_limit == 5000
        assert b.cost_limit_usd == 0.50
        assert b.wall_clock_limit_seconds == 120.0


class TestBudgetState:
    @pytest.fixture
    def state(self) -> BudgetState:
        return BudgetState(budget=Budget(token_limit=1000, cost_limit_usd=0.10))

    def test_initial_state_zero(self, state: BudgetState) -> None:
        assert state.tokens_used == 0
        assert state.cost_used_usd == 0.0

    def test_consume_tokens(self, state: BudgetState) -> None:
        state.consume(tokens=500, cost_usd=0.05)
        assert state.tokens_used == 500
        assert abs(state.cost_used_usd - 0.05) < 1e-9

    def test_remaining_tokens(self, state: BudgetState) -> None:
        state.consume(tokens=300)
        assert state.remaining_tokens == 700

    def test_remaining_cost(self, state: BudgetState) -> None:
        state.consume(cost_usd=0.03)
        assert abs(state.remaining_cost_usd - 0.07) < 1e-6

    def test_no_limit_remaining_tokens_is_none(self) -> None:
        state = BudgetState(budget=Budget())
        assert state.remaining_tokens is None

    def test_no_limit_remaining_cost_is_none(self) -> None:
        state = BudgetState(budget=Budget())
        assert state.remaining_cost_usd is None


class TestBudgetCheck:
    def test_no_limits_never_exceeded(self) -> None:
        state = BudgetState(budget=Budget())
        state.consume(tokens=999_999_999, cost_usd=9999.0)
        state.check()  # must not raise

    def test_token_limit_not_exceeded(self) -> None:
        state = BudgetState(budget=Budget(token_limit=1000))
        state.consume(tokens=999)
        state.check()  # under limit — must not raise

    def test_token_limit_exceeded_raises(self) -> None:
        state = BudgetState(budget=Budget(token_limit=1000))
        state.consume(tokens=1001)
        with pytest.raises(BudgetExceeded) as exc_info:
            state.check()
        assert exc_info.value.limit_type == "tokens"
        assert exc_info.value.current == 1001
        assert exc_info.value.limit == 1000

    def test_cost_limit_exceeded_raises(self) -> None:
        state = BudgetState(budget=Budget(cost_limit_usd=1.0))
        state.consume(cost_usd=1.01)
        with pytest.raises(BudgetExceeded) as exc_info:
            state.check()
        assert exc_info.value.limit_type == "cost_usd"

    def test_cost_limit_not_exceeded(self) -> None:
        state = BudgetState(budget=Budget(cost_limit_usd=1.0))
        state.consume(cost_usd=0.99)
        state.check()  # must not raise

    def test_wall_clock_exceeded_raises(self) -> None:
        state = BudgetState(budget=Budget(wall_clock_limit_seconds=0.01))
        time.sleep(0.05)  # exceed 10ms limit
        with pytest.raises(BudgetExceeded) as exc_info:
            state.check()
        assert exc_info.value.limit_type == "wall_clock_seconds"

    def test_wall_clock_not_exceeded(self) -> None:
        state = BudgetState(budget=Budget(wall_clock_limit_seconds=60.0))
        state.check()  # well under limit — must not raise

    def test_budget_exceeded_error_message(self) -> None:
        exc = BudgetExceeded(limit_type="tokens", current=1500.0, limit=1000.0)
        assert "tokens" in str(exc)
        assert "1500" in str(exc)
        assert "1000" in str(exc)


class TestBudgetIsExceeded:
    def test_is_exceeded_false_when_under(self) -> None:
        state = BudgetState(budget=Budget(token_limit=1000))
        state.consume(tokens=500)
        assert not state.is_exceeded()

    def test_is_exceeded_true_when_over(self) -> None:
        state = BudgetState(budget=Budget(token_limit=1000))
        state.consume(tokens=1001)
        assert state.is_exceeded()

    def test_is_exceeded_false_with_no_limits(self) -> None:
        state = BudgetState(budget=Budget())
        state.consume(tokens=999_999, cost_usd=9999.0)
        assert not state.is_exceeded()
