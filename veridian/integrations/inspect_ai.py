"""
veridian.integrations.inspect_ai
────────────────────────────────
**Preview** adapter exporting Veridian verification evidence in the
Inspect AI evaluation log format.

Support level: ``preview``. Verification boundary: this adapter is
read-only — it does not gate task transitions. It maps a Veridian
:class:`VerificationOutcome` (and the task that produced it) into an
Inspect-AI-compatible sample/scorer payload so an Inspect AI eval can
ingest Veridian evidence alongside its native traces.

The output dict shape mirrors Inspect AI's ``EvalSample`` structure with
the verifier outcome surfaced as a scorer entry. Consumers may serialize
it to JSONL for downstream Inspect AI tooling.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from typing import Any

from veridian.core.exceptions import VeridianError
from veridian.core.task import Task
from veridian.integrations.sdk import VerificationOutcome

__all__ = [
    "INSPECT_AI_SUPPORT_LEVEL",
    "InspectAIExportError",
    "InspectAIPreviewWarning",
    "export_outcome",
    "export_outcomes",
]


INSPECT_AI_SUPPORT_LEVEL: str = "preview"


class InspectAIExportError(VeridianError):
    """Inspect AI export error wrapped in the Veridian exception hierarchy."""


class InspectAIPreviewWarning(UserWarning):
    """Emitted on first export to signal preview support level."""


_warned = False


def _warn_once() -> None:
    global _warned
    if not _warned:
        warnings.warn(
            "Inspect AI export is a preview adapter; certified support is "
            "limited to LangGraph and CrewAI in v0.4.",
            InspectAIPreviewWarning,
            stacklevel=3,
        )
        _warned = True


def export_outcome(
    *,
    task: Task,
    outcome: VerificationOutcome,
    sample_id: str | None = None,
) -> dict[str, Any]:
    """Map a single Veridian verification outcome to an Inspect-AI-shaped sample.

    Args:
        task: The task whose verification contract was applied.
        outcome: The Veridian :class:`VerificationOutcome`.
        sample_id: Optional sample identifier; defaults to ``task.id``.

    Returns:
        A JSON-serializable dict mirroring Inspect AI's ``EvalSample`` shape
        with the verifier outcome encoded as a scorer entry.
    """
    _warn_once()
    if not task.id:
        raise InspectAIExportError("task.id must be set for Inspect AI export")

    return {
        "id": sample_id or task.id,
        "input": {
            "title": task.title,
            "description": task.description,
        },
        "target": None,
        "output": {
            "verifier_id": outcome.verifier_id,
            "passed": outcome.passed,
            "error": outcome.error,
        },
        "scores": {
            outcome.verifier_id: {
                "value": "C" if outcome.passed else "I",
                "answer": None,
                "explanation": outcome.error or "",
                "metadata": {
                    **outcome.evidence,
                    "score": outcome.score,
                },
            }
        },
        "metadata": {
            "veridian_task_id": task.id,
            "verifier_id": outcome.verifier_id,
        },
    }


def export_outcomes(
    pairs: Iterable[tuple[Task, VerificationOutcome]],
) -> list[dict[str, Any]]:
    """Map an iterable of ``(task, outcome)`` pairs to Inspect AI samples."""
    return [export_outcome(task=task, outcome=outcome) for task, outcome in pairs]
