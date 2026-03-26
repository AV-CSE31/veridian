"""
veridian.hooks.builtin
───────────────────────
Built-in hooks shipped with Veridian.
"""

from veridian.hooks.builtin.cost_guard import CostGuardHook
from veridian.hooks.builtin.cross_run_consistency import CrossRunConsistencyHook
from veridian.hooks.builtin.drift_detector import DriftDetectorHook
from veridian.hooks.builtin.human_review import HumanReviewHook
from veridian.hooks.builtin.logging_hook import LoggingHook
from veridian.hooks.builtin.rate_limit import RateLimitHook
from veridian.hooks.builtin.slack import SlackNotifyHook

__all__ = [
    "LoggingHook",
    "CostGuardHook",
    "HumanReviewHook",
    "RateLimitHook",
    "SlackNotifyHook",
    "CrossRunConsistencyHook",
    "DriftDetectorHook",
]
