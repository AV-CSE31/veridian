"""
Tests for veridian.eval.canary
──────────────────────────────
Canary Task Suite — held-out safety tests to detect silent regression.
TDD: RED phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from veridian.core.exceptions import CanaryRegressionError, VeridianConfigError
from veridian.eval.canary import (
    CanaryResult,
    CanarySuite,
    CanaryTask,
    CanaryReport,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_canary_task(
    task_id: str = "canary-1",
    title: str = "Safety refusal test",
    expected_pass: bool = True,
) -> CanaryTask:
    return CanaryTask(
        task_id=task_id,
        title=title,
        verifier_id="schema",
        verifier_config={"required_fields": ["answer"]},
        expected_pass=expected_pass,
    )


# ── CanaryTask ──────────────────────────────────────────────────────────────


class TestCanaryTask:
    def test_creates_canary_task(self) -> None:
        ct = _make_canary_task()
        assert ct.task_id == "canary-1"
        assert ct.expected_pass is True

    def test_to_dict(self) -> None:
        ct = _make_canary_task()
        d = ct.to_dict()
        assert d["task_id"] == "canary-1"
        assert d["verifier_id"] == "schema"

    def test_from_dict(self) -> None:
        d = {
            "task_id": "c2",
            "title": "Test",
            "verifier_id": "bash_exit",
            "verifier_config": {"command": "echo hi"},
            "expected_pass": True,
        }
        ct = CanaryTask.from_dict(d)
        assert ct.task_id == "c2"
        assert ct.verifier_id == "bash_exit"


# ── CanarySuite ─────────────────────────────────────────────────────────────


class TestCanarySuite:
    def test_creates_suite_from_tasks(self) -> None:
        tasks = [_make_canary_task(f"c{i}") for i in range(5)]
        suite = CanarySuite(tasks=tasks)
        assert len(suite.tasks) == 5

    def test_loads_suite_from_json_file(self, tmp_path: Path) -> None:
        data = [
            {
                "task_id": "c1",
                "title": "Test 1",
                "verifier_id": "schema",
                "verifier_config": {"required_fields": ["x"]},
                "expected_pass": True,
            },
            {
                "task_id": "c2",
                "title": "Test 2",
                "verifier_id": "bash_exit",
                "verifier_config": {"command": "true"},
                "expected_pass": True,
            },
        ]
        suite_file = tmp_path / "canary_suite.json"
        suite_file.write_text(json.dumps(data, indent=2))

        suite = CanarySuite.from_file(suite_file)
        assert len(suite.tasks) == 2

    def test_saves_suite_to_json_file(self, tmp_path: Path) -> None:
        tasks = [_make_canary_task(f"c{i}") for i in range(3)]
        suite = CanarySuite(tasks=tasks)
        path = tmp_path / "out.json"
        suite.save(path)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert len(loaded) == 3

    def test_rejects_empty_suite(self) -> None:
        with pytest.raises(VeridianConfigError, match="empty"):
            CanarySuite(tasks=[])

    def test_rejects_duplicate_task_ids(self) -> None:
        tasks = [_make_canary_task("c1"), _make_canary_task("c1")]
        with pytest.raises(VeridianConfigError, match="duplicate"):
            CanarySuite(tasks=tasks)


# ── CanaryResult ────────────────────────────────────────────────────────────


class TestCanaryResult:
    def test_no_regression_when_all_pass(self) -> None:
        result = CanaryResult(
            task_id="c1",
            expected_pass=True,
            actual_pass=True,
        )
        assert result.regression is False

    def test_regression_when_expected_pass_but_fails(self) -> None:
        result = CanaryResult(
            task_id="c1",
            expected_pass=True,
            actual_pass=False,
        )
        assert result.regression is True

    def test_no_regression_when_expected_fail_and_fails(self) -> None:
        result = CanaryResult(
            task_id="c1",
            expected_pass=False,
            actual_pass=False,
        )
        assert result.regression is False

    def test_to_dict(self) -> None:
        result = CanaryResult(task_id="c1", expected_pass=True, actual_pass=False)
        d = result.to_dict()
        assert d["regression"] is True


# ── CanaryReport ────────────────────────────────────────────────────────────


class TestCanaryReport:
    def test_report_with_no_regressions(self) -> None:
        results = [
            CanaryResult(task_id="c1", expected_pass=True, actual_pass=True),
            CanaryResult(task_id="c2", expected_pass=True, actual_pass=True),
        ]
        report = CanaryReport(results=results, run_id="r1")
        assert report.regression_count == 0
        assert report.passed is True

    def test_report_detects_regressions(self) -> None:
        results = [
            CanaryResult(task_id="c1", expected_pass=True, actual_pass=True),
            CanaryResult(task_id="c2", expected_pass=True, actual_pass=False),
            CanaryResult(task_id="c3", expected_pass=True, actual_pass=False),
        ]
        report = CanaryReport(results=results, run_id="r1")
        assert report.regression_count == 2
        assert report.passed is False
        assert report.regressed_task_ids == ["c2", "c3"]

    def test_report_to_dict(self) -> None:
        results = [CanaryResult(task_id="c1", expected_pass=True, actual_pass=True)]
        report = CanaryReport(results=results, run_id="r1")
        d = report.to_dict()
        assert "passed" in d
        assert "regression_count" in d

    def test_report_to_markdown(self) -> None:
        results = [
            CanaryResult(task_id="c1", expected_pass=True, actual_pass=True),
            CanaryResult(task_id="c2", expected_pass=True, actual_pass=False),
        ]
        report = CanaryReport(results=results, run_id="r1")
        md = report.to_markdown()
        assert "c2" in md
        assert "regression" in md.lower()

    def test_raise_on_regression(self) -> None:
        results = [
            CanaryResult(task_id="c1", expected_pass=True, actual_pass=False),
        ]
        report = CanaryReport(results=results, run_id="r1")
        with pytest.raises(CanaryRegressionError, match="c1"):
            report.raise_on_regression()

    def test_no_raise_when_all_pass(self) -> None:
        results = [
            CanaryResult(task_id="c1", expected_pass=True, actual_pass=True),
        ]
        report = CanaryReport(results=results, run_id="r1")
        report.raise_on_regression()  # should not raise
