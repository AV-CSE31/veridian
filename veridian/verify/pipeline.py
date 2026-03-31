"""
veridian.verify.pipeline
─────────────────────────
Streaming Verification Pipeline — chain multiple verifiers with
short-circuit logic and async streaming.

Features:
- Sequential pipeline stages (ordered list of verifiers)
- Short-circuit: stop on first failure (configurable)
- Conditional branching: each stage can have a condition predicate
- Async streaming: results flow through as stages complete
- Per-stage timing in milliseconds
- Build from dict/YAML config

Usage::

    pipeline = VerificationPipeline(config=PipelineConfig(short_circuit=True))
    pipeline.add_stage(PipelineStage("schema_check", SchemaVerifier(...)))
    pipeline.add_stage(PipelineStage("llm_judge", LLMJudgeVerifier(...)))

    result = pipeline.run(task, task_result)
    if result.passed:
        ...

    # Async streaming
    async for stage_result in pipeline.stream(task, task_result):
        print(stage_result.stage_name, stage_result.result.passed)

    # From config dict
    pipeline = VerificationPipeline.from_config({
        "short_circuit": True,
        "stages": [
            {"name": "schema", "verifier_id": "schema", "config": {...}},
        ]
    })
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any

from veridian.core.exceptions import PipelineError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """
    Configuration for VerificationPipeline.

    Args:
        short_circuit: If True, stop after the first failing stage (default True).
    """

    short_circuit: bool = True


@dataclass
class PipelineStage:
    """
    One stage in a verification pipeline.

    Args:
        name:       Human-readable stage name.
        verifier:   BaseVerifier instance to run.
        condition:  Optional predicate (task, result) → bool.
                    If False, the stage is skipped (counts as passed).
    """

    name: str
    verifier: BaseVerifier
    condition: Callable[[Task, TaskResult], bool] | None = None

    def should_run(self, task: Task, result: TaskResult) -> bool:
        if self.condition is None:
            return True
        return self.condition(task, result)


@dataclass
class StageResult:
    """
    The outcome of a single pipeline stage.

    Args:
        stage_name:  Name of the PipelineStage.
        result:      VerificationResult, or None if skipped.
        duration_ms: Wall-clock time in milliseconds.
        skipped:     True if the stage condition evaluated to False.
    """

    stage_name: str
    result: VerificationResult | None
    duration_ms: float = 0.0
    skipped: bool = False


@dataclass
class PipelineResult:
    """
    Aggregated result from all pipeline stages.

    passed:          True iff no stage failed (skipped stages are neutral).
    stage_results:   Ordered list of per-stage outcomes.
    total_duration_ms: Sum of all stage durations.
    """

    stage_results: list[StageResult]

    @property
    def passed(self) -> bool:
        return all(
            sr.result is None or sr.skipped or sr.result.passed
            for sr in self.stage_results
        )

    @property
    def total_duration_ms(self) -> float:
        return sum(
            sr.duration_ms for sr in self.stage_results if not sr.skipped
        )

    @property
    def first_failure(self) -> StageResult | None:
        for sr in self.stage_results:
            if not sr.skipped and sr.result is not None and not sr.result.passed:
                return sr
        return None


class VerificationPipeline:
    """
    Chain multiple verifiers in a sequential pipeline.

    Stages are run in order. Each stage's timing is measured independently.
    Async streaming yields StageResult objects as each stage completes.
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self._stages: list[PipelineStage] = []

    # ── Configuration ──────────────────────────────────────────────────────────

    def add_stage(self, stage: PipelineStage) -> None:
        """Append a stage to the pipeline."""
        self._stages.append(stage)

    @classmethod
    def from_config(cls, config_dict: dict[str, Any]) -> VerificationPipeline:
        """
        Build a VerificationPipeline from a dict (e.g. parsed from YAML).

        Expected format::

            {
              "short_circuit": true,
              "stages": [
                {"name": "stage_name", "verifier_id": "schema", "config": {}},
              ]
            }
        """
        from veridian.verify.base import registry  # noqa: PLC0415

        short_circuit = config_dict.get("short_circuit", True)
        pipeline = cls(config=PipelineConfig(short_circuit=short_circuit))

        for stage_cfg in config_dict.get("stages", []):
            verifier_id: str = stage_cfg.get("verifier_id", "")
            if not verifier_id:
                raise PipelineError(
                    f"Pipeline stage '{stage_cfg.get('name', '?')}' "
                    f"is missing 'verifier_id'."
                )
            verifier_config: dict[str, Any] | None = stage_cfg.get("config")
            verifier = registry.get(verifier_id, verifier_config)
            stage = PipelineStage(
                name=stage_cfg.get("name", verifier_id),
                verifier=verifier,
            )
            pipeline.add_stage(stage)

        return pipeline

    # ── Synchronous run ────────────────────────────────────────────────────────

    def run(self, task: Task, result: TaskResult) -> PipelineResult:
        """
        Execute all stages synchronously.

        Respects short_circuit: stops on first failure if enabled.
        Returns PipelineResult with all stage outcomes.
        """
        stage_results: list[StageResult] = []

        for stage in self._stages:
            sr = self._run_stage(stage, task, result)
            stage_results.append(sr)

            if (
                self.config.short_circuit
                and not sr.skipped
                and sr.result is not None
                and not sr.result.passed
            ):
                log.debug(
                    "pipeline.short_circuit stage=%s",
                    stage.name,
                )
                break

        return PipelineResult(stage_results=stage_results)

    def _run_stage(
        self, stage: PipelineStage, task: Task, result: TaskResult
    ) -> StageResult:
        if not stage.should_run(task, result):
            return StageResult(stage_name=stage.name, result=None, skipped=True)

        t0 = time.perf_counter()
        try:
            vr = stage.verifier.verify(task, result)
        except Exception as exc:
            # Verifier raised an internal exception → treat as failure
            log.error(
                "pipeline.stage.error stage=%s err=%s",
                stage.name,
                exc,
            )
            vr = VerificationResult(
                passed=False,
                error=f"Stage '{stage.name}' raised {type(exc).__name__}: {exc}"[:300],
            )
        duration_ms = (time.perf_counter() - t0) * 1000

        log.debug(
            "pipeline.stage.done stage=%s passed=%s duration_ms=%.1f",
            stage.name,
            vr.passed,
            duration_ms,
        )
        return StageResult(
            stage_name=stage.name,
            result=vr,
            duration_ms=duration_ms,
        )

    # ── Async API ──────────────────────────────────────────────────────────────

    async def run_async(self, task: Task, result: TaskResult) -> PipelineResult:
        """
        Execute all stages asynchronously (in an executor to avoid blocking).

        Respects short_circuit like the synchronous run.
        """
        stage_results: list[StageResult] = []
        loop = asyncio.get_event_loop()

        for stage in self._stages:
            sr = await loop.run_in_executor(
                None, self._run_stage, stage, task, result
            )
            stage_results.append(sr)

            if (
                self.config.short_circuit
                and not sr.skipped
                and sr.result is not None
                and not sr.result.passed
            ):
                break

        return PipelineResult(stage_results=stage_results)

    async def stream(
        self, task: Task, result: TaskResult
    ) -> AsyncGenerator[StageResult, None]:
        """
        Async generator: yield StageResult as each stage completes.

        Does NOT short-circuit — streams all stages so callers receive
        a complete picture.
        """
        loop = asyncio.get_event_loop()

        for stage in self._stages:
            sr = await loop.run_in_executor(
                None, self._run_stage, stage, task, result
            )
            yield sr


__all__ = [
    "PipelineStage",
    "PipelineConfig",
    "StageResult",
    "PipelineResult",
    "VerificationPipeline",
]
