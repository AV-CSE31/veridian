"""
tests/unit/test_cost.py
────────────────────────
Tests for A3: Cost tracking per agent run.
"""

from __future__ import annotations

import pytest

from veridian.cost import (
    BUILTIN_PRICING,
    CostEntry,
    CostTracker,
    ModelPricing,
    compute_cost,
)


class TestModelPricing:
    def test_has_input_and_output_price(self) -> None:
        p = ModelPricing(input_per_1k=0.003, output_per_1k=0.015)
        assert p.input_per_1k == 0.003
        assert p.output_per_1k == 0.015

    def test_cost_for_zero_tokens(self) -> None:
        p = ModelPricing(input_per_1k=0.003, output_per_1k=0.015)
        assert p.cost_usd(input_tokens=0, output_tokens=0) == 0.0

    def test_cost_calculation(self) -> None:
        p = ModelPricing(input_per_1k=2.0, output_per_1k=10.0)
        # 1000 input = $2.00, 500 output = $5.00 → $7.00
        cost = p.cost_usd(input_tokens=1000, output_tokens=500)
        assert abs(cost - 7.0) < 1e-9

    def test_fractional_tokens(self) -> None:
        p = ModelPricing(input_per_1k=1.0, output_per_1k=2.0)
        # 100 input = $0.10, 100 output = $0.20 → $0.30
        cost = p.cost_usd(input_tokens=100, output_tokens=100)
        assert abs(cost - 0.30) < 1e-9


class TestBuiltinPricing:
    def test_claude_present(self) -> None:
        keys = list(BUILTIN_PRICING.keys())
        assert any("claude" in k.lower() for k in keys)

    def test_gpt_present(self) -> None:
        keys = list(BUILTIN_PRICING.keys())
        assert any("gpt" in k.lower() for k in keys)

    def test_all_entries_are_model_pricing(self) -> None:
        for k, v in BUILTIN_PRICING.items():
            assert isinstance(v, ModelPricing), f"{k} is not a ModelPricing"


class TestComputeCost:
    def test_known_model(self) -> None:
        model = next(iter(BUILTIN_PRICING))
        cost = compute_cost(model, input_tokens=1000, output_tokens=500)
        assert cost >= 0.0

    def test_unknown_model_fallback(self) -> None:
        cost = compute_cost("totally-unknown-model-xyz", input_tokens=1000, output_tokens=500)
        assert cost >= 0.0

    def test_zero_tokens_zero_cost(self) -> None:
        cost = compute_cost("gpt-4", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_partial_model_name_match(self) -> None:
        cost = compute_cost("gpt-4-turbo", input_tokens=1000, output_tokens=500)
        assert cost >= 0.0


class TestCostEntry:
    def test_fields(self) -> None:
        entry = CostEntry(
            task_id="t-001",
            run_id="run-001",
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05,
        )
        assert entry.task_id == "t-001"
        assert entry.run_id == "run-001"
        assert entry.model == "gpt-4"
        assert entry.cost_usd == 0.05

    def test_total_tokens(self) -> None:
        entry = CostEntry(
            task_id="t-001",
            run_id="run-001",
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05,
        )
        assert entry.total_tokens == 150

    def test_to_dict(self) -> None:
        entry = CostEntry(
            task_id="t-001",
            run_id="run-001",
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05,
        )
        d = entry.to_dict()
        assert d["task_id"] == "t-001"
        assert d["cost_usd"] == 0.05
        assert d["total_tokens"] == 150


class TestCostTracker:
    @pytest.fixture
    def tracker(self) -> CostTracker:
        return CostTracker(run_id="run-001")

    def test_empty_tracker_has_zero_total(self, tracker: CostTracker) -> None:
        assert tracker.total_usd == 0.0
        assert tracker.total_tokens == 0

    def test_record_single_entry(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=100, output_tokens=50)
        assert len(tracker.entries) == 1
        assert tracker.total_tokens == 150

    def test_record_multiple_entries(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=100, output_tokens=50)
        tracker.record(task_id="t-002", model="gpt-4", input_tokens=200, output_tokens=100)
        assert len(tracker.entries) == 2
        assert tracker.total_tokens == 450

    def test_total_usd_accumulates(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=1000, output_tokens=500)
        tracker.record(task_id="t-002", model="gpt-4", input_tokens=1000, output_tokens=500)
        single = compute_cost("gpt-4", input_tokens=1000, output_tokens=500)
        assert abs(tracker.total_usd - 2 * single) < 1e-9

    def test_by_task_groups_correctly(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=100, output_tokens=50)
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=200, output_tokens=100)
        tracker.record(task_id="t-002", model="gpt-4", input_tokens=50, output_tokens=25)
        by_task = tracker.by_task()
        assert len(by_task["t-001"]) == 2
        assert len(by_task["t-002"]) == 1

    def test_to_dict(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=100, output_tokens=50)
        d = tracker.to_dict()
        assert d["run_id"] == "run-001"
        assert "total_usd" in d
        assert "entries" in d

    def test_run_id_propagates_to_entries(self, tracker: CostTracker) -> None:
        tracker.record(task_id="t-001", model="gpt-4", input_tokens=100, output_tokens=50)
        assert tracker.entries[0].run_id == "run-001"
