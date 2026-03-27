"""
veridian.testing
────────────────
A4: Record-replay test harness for agent executions.

Usage::

    from veridian.testing import AgentRecorder, Replayer, ReplayAssertion, RecordedRun
"""

from veridian.testing.recorder import AgentRecorder, RecordedRun
from veridian.testing.replayer import ReplayAssertion, Replayer, ReplayResult

__all__ = [
    "AgentRecorder",
    "RecordedRun",
    "ReplayAssertion",
    "ReplayResult",
    "Replayer",
]
