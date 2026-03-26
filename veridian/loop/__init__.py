"""
veridian.loop
──────────────
Task execution loops: synchronous VeridianRunner and async ParallelRunner.
"""

from veridian.loop.parallel_runner import ParallelRunner
from veridian.loop.runner import RunSummary, VeridianRunner

__all__ = ["VeridianRunner", "RunSummary", "ParallelRunner"]
