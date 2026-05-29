"""
veridian.loop.run_controller
---------------------------------------------------------------
Run-scoped lifecycle helper extracted from VeridianRunner.

Owns the parts of the runner that are NOT about per-task processing:
  - SIGINT installation / restoration
  - the ``_shutdown`` flag that the task loop polls
  - firing RunStarted / RunCompleted hook events
  - pinning the observability trace id

VeridianRunner.run() composes a _RunController with a _TaskDispatcher;
the frozen runner sequence (crash recovery -> RunStarted -> task loop ->
RunCompleted) remains a single linear method in VeridianRunner so the
contract documented at the top of runner.py stays auditable in one
place.
"""

from __future__ import annotations

import contextlib
import logging
import signal as _signal
from types import FrameType
from typing import Any

from veridian.core.events import RunCompleted, RunStarted
from veridian.hooks.registry import HookRegistry

__all__ = ["_RunController"]

log = logging.getLogger(__name__)


class _RunController:
    """Lifecycle helper for a single VeridianRunner.run() invocation.

    Intentionally package-private: the runner is the public surface.
    Tests that need to mock lifecycle behaviour should mock the runner
    methods that delegate here.
    """

    def __init__(self, hooks: HookRegistry) -> None:
        self.hooks = hooks
        self._shutdown = False
        self._previous_sigint: Any = None

    # ------ shutdown flag ----------------------------------------------------
    @property
    def shutdown(self) -> bool:
        return self._shutdown

    def request_shutdown(self) -> None:
        self._shutdown = True

    # ------ signal handling --------------------------------------------------
    def install_signal_handler(self) -> None:
        """Install SIGINT handler that flips the shutdown flag.

        Saves the previous handler so :meth:`restore_signal_handler` can
        put it back, even on the exception path. Silently no-ops when
        running in a non-main thread (where signal.signal raises).
        """

        def _handler(signum: int, frame: FrameType | None) -> None:
            log.warning("runner.sigint_received --- will stop after current task")
            self._shutdown = True

        try:
            self._previous_sigint = _signal.signal(_signal.SIGINT, _handler)
        except (OSError, ValueError):
            self._previous_sigint = None

    def restore_signal_handler(self) -> None:
        """Restore the SIGINT handler captured by ``install_signal_handler``."""
        if self._previous_sigint is None:
            return
        with contextlib.suppress(OSError, ValueError, TypeError):
            _signal.signal(_signal.SIGINT, self._previous_sigint)
        self._previous_sigint = None

    # ------ run lifecycle events --------------------------------------------
    def fire_run_started(self, run_id: str, total_tasks: int, phase: str | None) -> None:
        self.hooks.fire(
            "before_run",
            RunStarted(run_id=run_id, total_tasks=total_tasks, phase=phase),
        )

    def fire_run_completed(self, run_id: str, summary: Any) -> None:
        self.hooks.fire("after_run", RunCompleted(run_id=run_id, summary=summary))
