"""
tests.test_pipeline
───────────────────
Tests for F3.5 — Streaming Verification Pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult
from veridian.verify.pipeline import (
    PipelineStage,
    VerificationPipeline,
    PipelineResult,
    PipelineConfig,
    StageResult,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_task(**kwargs: Any) -> Task:
    return Task(title="test", **kwargs)


def make_result(raw: str = "output") -> TaskResult:
    return TaskResult(raw_output=raw)


class PassVerifier(BaseVerifier):
    id = "pass_verifier"
    description = "Always passes"

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True, evidence={"source": "pass_verifier"})


class FailVerifier(BaseVerifier):
    id = "fail_verifier"
    description = "Always fails"

    def __init__(self, error: str = "deliberate failure") -> None:
        self.error = error

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error=self.error)


class SlowVerifier(BaseVerifier):
    id = "slow_verifier"
    description = "Simulates slow work"

    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        import time
        time.sleep(self.delay)
        return VerificationResult(passed=True)


# ─── PipelineStage tests ──────────────────────────────────────────────────────


class TestPipelineStage:
    def test_creation(self) -> None:
        v = PassVerifier()
        stage = PipelineStage(name="stage_1", verifier=v)
        assert stage.name == "stage_1"
        assert stage.verifier is v

    def test_optional_condition(self) -> None:
        v = PassVerifier()
        stage = PipelineStage(
            name="stage_1",
            verifier=v,
            condition=lambda task, result: "json" in result.raw_output,
        )
        task = make_task()
        # Condition false → stage is skipped
        result_no_json = make_result("plain text")
        assert stage.should_run(task, result_no_json) is False

        result_json = make_result('json {"key": "val"}')
        assert stage.should_run(task, result_json) is True

    def test_no_condition_always_runs(self) -> None:
        stage = PipelineStage(name="s", verifier=PassVerifier())
        assert stage.should_run(make_task(), make_result()) is True


# ─── StageResult tests ────────────────────────────────────────────────────────


class TestStageResult:
    def test_creation(self) -> None:
        vr = VerificationResult(passed=True)
        sr = StageResult(stage_name="s1", result=vr, duration_ms=10.5)
        assert sr.stage_name == "s1"
        assert sr.duration_ms == 10.5
        assert sr.skipped is False

    def test_skipped(self) -> None:
        sr = StageResult(stage_name="s1", result=None, skipped=True)
        assert sr.skipped is True
        assert sr.result is None


# ─── PipelineResult tests ─────────────────────────────────────────────────────


class TestPipelineResult:
    def test_all_pass(self) -> None:
        stages = [
            StageResult("s1", VerificationResult(passed=True), duration_ms=5.0),
            StageResult("s2", VerificationResult(passed=True), duration_ms=3.0),
        ]
        pr = PipelineResult(stage_results=stages)
        assert pr.passed is True
        assert pr.total_duration_ms == 8.0

    def test_any_fail(self) -> None:
        stages = [
            StageResult("s1", VerificationResult(passed=True), duration_ms=5.0),
            StageResult("s2", VerificationResult(passed=False, error="bad"), duration_ms=2.0),
        ]
        pr = PipelineResult(stage_results=stages)
        assert pr.passed is False

    def test_skipped_stages_dont_count_as_fail(self) -> None:
        stages = [
            StageResult("s1", VerificationResult(passed=True), duration_ms=5.0),
            StageResult("s2", result=None, skipped=True),
        ]
        pr = PipelineResult(stage_results=stages)
        assert pr.passed is True

    def test_first_failure(self) -> None:
        stages = [
            StageResult("s1", VerificationResult(passed=True), duration_ms=1.0),
            StageResult("s2", VerificationResult(passed=False, error="fail s2"), duration_ms=1.0),
            StageResult("s3", VerificationResult(passed=False, error="fail s3"), duration_ms=1.0),
        ]
        pr = PipelineResult(stage_results=stages)
        assert pr.first_failure is not None
        assert pr.first_failure.stage_name == "s2"


# ─── VerificationPipeline tests ───────────────────────────────────────────────


class TestVerificationPipeline:
    def test_empty_pipeline_passes(self) -> None:
        p = VerificationPipeline()
        pr = p.run(make_task(), make_result())
        assert pr.passed is True

    def test_single_pass_stage(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", PassVerifier()))
        pr = p.run(make_task(), make_result())
        assert pr.passed is True
        assert len(pr.stage_results) == 1

    def test_single_fail_stage(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", FailVerifier("test error")))
        pr = p.run(make_task(), make_result())
        assert pr.passed is False

    def test_short_circuit_on_failure(self) -> None:
        log: list[str] = []

        class LogVerifier(BaseVerifier):
            id = "log_verifier"

            def __init__(self, name: str, passed: bool) -> None:
                self.name = name
                self._passed = passed

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                log.append(self.name)
                return VerificationResult(passed=self._passed)

        p = VerificationPipeline(config=PipelineConfig(short_circuit=True))
        p.add_stage(PipelineStage("s1", LogVerifier("s1", True)))
        p.add_stage(PipelineStage("s2", LogVerifier("s2", False)))
        p.add_stage(PipelineStage("s3", LogVerifier("s3", True)))

        pr = p.run(make_task(), make_result())
        assert pr.passed is False
        assert "s3" not in log  # short-circuited

    def test_no_short_circuit_runs_all(self) -> None:
        log: list[str] = []

        class LogVerifier(BaseVerifier):
            id = "log_verifier2"

            def __init__(self, name: str, passed: bool) -> None:
                self.name = name
                self._passed = passed

            def verify(self, task: Task, result: TaskResult) -> VerificationResult:
                log.append(self.name)
                return VerificationResult(passed=self._passed)

        p = VerificationPipeline(config=PipelineConfig(short_circuit=False))
        p.add_stage(PipelineStage("s1", LogVerifier("s1", True)))
        p.add_stage(PipelineStage("s2", LogVerifier("s2", False)))
        p.add_stage(PipelineStage("s3", LogVerifier("s3", True)))

        pr = p.run(make_task(), make_result())
        assert "s1" in log
        assert "s2" in log
        assert "s3" in log

    def test_conditional_stage_skipped(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage(
            "conditional",
            FailVerifier("should not fail"),
            condition=lambda t, r: False,
        ))
        pr = p.run(make_task(), make_result())
        assert pr.passed is True
        assert pr.stage_results[0].skipped is True

    def test_stage_timing_recorded(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", SlowVerifier(0.01)))
        pr = p.run(make_task(), make_result())
        assert pr.stage_results[0].duration_ms >= 1.0

    def test_from_config_dict(self) -> None:
        """Pipeline can be built from a dict config."""
        from veridian.verify.base import registry
        registry.register(PassVerifier)

        config_dict = {
            "short_circuit": True,
            "stages": [
                {"name": "stage_1", "verifier_id": "pass_verifier"},
            ],
        }
        p = VerificationPipeline.from_config(config_dict)
        pr = p.run(make_task(), make_result())
        assert pr.passed is True

    def test_pipeline_result_has_per_stage_timing(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", PassVerifier()))
        p.add_stage(PipelineStage("s2", PassVerifier()))
        pr = p.run(make_task(), make_result())
        for sr in pr.stage_results:
            assert sr.duration_ms >= 0.0

    @pytest.mark.asyncio
    async def test_async_run(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", PassVerifier()))
        p.add_stage(PipelineStage("s2", PassVerifier()))
        pr = await p.run_async(make_task(), make_result())
        assert pr.passed is True
        assert len(pr.stage_results) == 2

    @pytest.mark.asyncio
    async def test_async_streaming_yields_results(self) -> None:
        p = VerificationPipeline()
        p.add_stage(PipelineStage("s1", PassVerifier()))
        p.add_stage(PipelineStage("s2", FailVerifier()))
        p.add_stage(PipelineStage("s3", PassVerifier()))

        results: list[StageResult] = []
        async for sr in p.stream(make_task(), make_result()):
            results.append(sr)

        assert len(results) == 3  # all stages streamed (no short-circuit by default in stream)
        assert results[0].stage_name == "s1"
        assert results[1].stage_name == "s2"
