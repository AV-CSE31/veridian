"""
veridian.hooks.builtin
───────────────────────
Built-in hooks shipped with Veridian.
"""

from veridian.hooks.builtin.adaptive_safety import AdaptiveSafetyHook
from veridian.hooks.builtin.anomaly_detector import AnomalyDetectorHook
from veridian.hooks.builtin.behavioral_fingerprint import BehavioralFingerprintHook
from veridian.hooks.builtin.boundary_fluidity import BoundaryFluidityHook
from veridian.hooks.builtin.cost_guard import CostGuardHook
from veridian.hooks.builtin.cross_run_consistency import CrossRunConsistencyHook
from veridian.hooks.builtin.drift_detector import DriftDetectorHook
from veridian.hooks.builtin.evolution_monitor import EvolutionMonitorHook
from veridian.hooks.builtin.human_review import HumanReviewHook
from veridian.hooks.builtin.identity_guard import IdentityGuardHook
from veridian.hooks.builtin.logging_hook import LoggingHook
from veridian.hooks.builtin.rate_limit import RateLimitHook
from veridian.hooks.builtin.slack import SlackNotifyHook

__all__ = [
    "LoggingHook",
    "BoundaryFluidityHook",
    "IdentityGuardHook",
    "AdaptiveSafetyHook",
    "CostGuardHook",
    "HumanReviewHook",
    "RateLimitHook",
    "SlackNotifyHook",
    "CrossRunConsistencyHook",
    "AnomalyDetectorHook",
    "EvolutionMonitorHook",
    "BehavioralFingerprintHook",
    "DriftDetectorHook",
]
