"""
veridian.hooks.builtin.repetition_guard
---------------------------------------------------------------
RepetitionGuardHook --- halts the run when N consecutive task outputs
hash identically. Catches stuck loops before they burn budget.

Routes through :class:`veridian.core.exceptions.RepetitionDetected`,
which is a :class:`RunAbortRequested` subclass.

Fingerprint sources (in priority order):
  1. ``result.structured`` if non-empty (most stable)
  2. ``result.raw_output`` trimmed
The fingerprint is sha256-hex; a sliding window of the last ``window``
fingerprints is compared element-wise.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from typing import Any, ClassVar

from veridian.core.exceptions import RepetitionDetected
from veridian.hooks.base import BaseHook

__all__ = ["RepetitionGuardHook"]

log = logging.getLogger(__name__)


class RepetitionGuardHook(BaseHook):
    """Detect oscillation across a window of recent task outputs.

    Default behaviour: halt when the last 3 task outputs hash identically.
    Raise the window for noisier workloads (e.g. 5 for retries that vary
    superficially); lower it to 2 only if you accept false positives.
    """

    id: ClassVar[str] = "repetition_guard"
    priority: ClassVar[int] = 45

    def __init__(self, window: int = 3) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self.window = window
        self._fps: deque[str] = deque(maxlen=window)

    def after_task(self, event: Any) -> None:
        task = getattr(event, "task", None)
        if task is None:
            return
        result = getattr(task, "result", None)
        if result is None:
            return
        fp = self._fingerprint(result)
        if not fp:
            return
        self._fps.append(fp)
        if len(self._fps) < self.window:
            return
        first = self._fps[0]
        if all(item == first for item in self._fps):
            log.warning(
                "repetition_guard.tripped window=%d fp=%s task_id=%s",
                self.window,
                first[:8],
                getattr(task, "id", "?"),
            )
            raise RepetitionDetected(window=self.window, fingerprint=first)

    def _fingerprint(self, result: Any) -> str:
        structured = getattr(result, "structured", None)
        if isinstance(structured, dict) and structured:
            try:
                blob = json.dumps(structured, sort_keys=True, default=str)
            except (TypeError, ValueError):
                blob = repr(structured)
        else:
            raw = getattr(result, "raw_output", "") or ""
            blob = raw.strip()
        if not blob:
            return ""
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
