"""
chit.hooks
------------------------------------------
Hook infrastructure: BaseHook ABC, HookRegistry, and builtin hooks.
"""

from chit.hooks.base import BaseHook
from chit.hooks.registry import HookRegistry

__all__ = ["BaseHook", "HookRegistry"]
