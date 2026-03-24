"""
veridian.hooks
──────────────
Hook infrastructure: BaseHook ABC, HookRegistry, and builtin hooks.
"""
from veridian.hooks.base import BaseHook
from veridian.hooks.registry import HookRegistry

__all__ = ["BaseHook", "HookRegistry"]
