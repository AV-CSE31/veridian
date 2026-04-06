from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from veridian.core.config import VeridianConfig
from veridian.core.task import PRMBudget, PRMRunResult, PRMScore, Task, TraceStep
from veridian.ledger.ledger import TaskLedger
from veridian.loop.runner import VeridianRunner
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider
from veridian.verify.base import PRMVerifier, VerifierRegistry
from veridian.verify.builtin.schema import SchemaVerifier


class DemoPRMVerifier(PRMVerifier):
    id: ClassVar[str] = "demo_prm"
    description: ClassVar[str] = "Demo PRM verifier used for policy repair/block example."

    def score_steps(
        self,
        *,
        task_id: str,
        steps: list[TraceStep],
        context: dict[str, Any],
        budget: PRMBudget,
    ) -> PRMRunResult:
        _ = budget
        repair_attempts = int(context.get("repair_attempts", 0))
        combined = " ".join(step.content.lower() for step in steps)
        is_uncertain = "maybe" in combined or "not sure" in combined
        score = 0.35 if is_uncertain else 0.92
        if repair_attempts > 0:
            score = 0.94
        confidence = 0.75 if is_uncertain else 0.9
        if repair_attempts > 0:
            confidence = 0.91

        scored = [
            PRMScore(
                step_id=step.step_id,
                score=score,
                confidence=confidence,
                model_id="demo_prm",
                version="1",
                failure_mode="uncertain_reasoning" if is_uncertain else None,
            )
            for step in steps
        ]
        return PRMRunResult(
            passed=score >= 0.8 and confidence >= 0.6,
            aggregate_score=score,
            aggregate_confidence=confidence,
            threshold=0.8,
            scored_steps=scored,
            policy_action="allow",
            repair_hint="Replace uncertain wording with concrete, verifiable claims.",
            error=None if score >= 0.8 else "uncertain_reasoning",
        )


def make_response(summary: str, body: str) -> LLMResponse:
    payload = json.dumps({"summary": summary, "structured": {"summary": summary}, "artifacts": []})
    return LLMResponse(
        content=f"{body}\n<veridian:result>\n{payload}\n</veridian:result>",
        input_tokens=64,
        output_tokens=32,
        model="mock",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="veridian_prm_demo_") as tmp:
        tmp_path = Path(tmp)
        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
        )
        ledger = TaskLedger(path=config.ledger_file, progress_file=str(config.progress_file))
        provider = MockProvider()

        registry = VerifierRegistry()
        registry.register_many(SchemaVerifier, DemoPRMVerifier)

        task_repair = Task(
            id="demo-repair",
            title="Repair case",
            description="Should repair once and complete.",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary"]},
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "demo_prm",
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "retry_with_repair",
                    "max_repairs": 1,
                    "strict_replay": True,
                }
            },
        )
        task_block = Task(
            id="demo-block",
            title="Block case",
            description="Should be blocked by PRM policy.",
            verifier_id="schema",
            verifier_config={"required_fields": ["summary"]},
            metadata={
                "prm": {
                    "enabled": True,
                    "verifier_id": "demo_prm",
                    "threshold": 0.8,
                    "min_confidence": 0.6,
                    "action_below_threshold": "block",
                    "strict_replay": True,
                }
            },
        )
        ledger.add([task_repair])
        provider.script([make_response("repair attempt 1", "Maybe this is correct.")])
        provider.script(
            [make_response("repair attempt 2", "Implemented fix with deterministic checks.")]
        )

        runner = VeridianRunner(
            ledger=ledger,
            provider=provider,
            config=config,
            verifier_registry=registry,
        )
        repair_summary = runner.run()
        print(
            f"Repair run complete: done={repair_summary.done_count}, "
            f"failed={repair_summary.failed_count}"
        )

        ledger.add([task_block])
        provider.script([make_response("block attempt", "Maybe this is correct.")])
        block_summary = runner.run()
        print(
            f"Block run complete: done={block_summary.done_count}, "
            f"failed={block_summary.failed_count}"
        )

        for task_id in ("demo-repair", "demo-block"):
            task = ledger.get(task_id)
            policy_action = (
                task.result.prm_result.policy_action
                if task.result and task.result.prm_result
                else ""
            )
            print(f"{task_id}: status={task.status.value}, policy_action={policy_action}")


if __name__ == "__main__":
    main()
