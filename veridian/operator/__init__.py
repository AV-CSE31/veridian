"""
veridian.operator
──────────────────
Operator plane — operational tooling for human operators managing Veridian runs.

Provides timeline views, approval queues, replay diffing, DLQ triage,
and incident runbooks for production observability and incident response.
"""

from veridian.operator.approvals import ApprovalQueue, ApprovalRequest
from veridian.operator.dlq_triage import DLQTriageView, FailureCategory
from veridian.operator.replay import OperatorReplay, ReplayDiff
from veridian.operator.runbooks import Runbook, RunbookRegistry
from veridian.operator.timeline import RunTimeline, TimelineEntry

__all__ = [
    "ApprovalQueue",
    "ApprovalRequest",
    "DLQTriageView",
    "FailureCategory",
    "OperatorReplay",
    "ReplayDiff",
    "Runbook",
    "RunbookRegistry",
    "RunTimeline",
    "TimelineEntry",
]
