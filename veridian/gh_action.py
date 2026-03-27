"""
veridian.gh_action
───────────────────
A5: GitHub Action entrypoint for CI/CD verification.

This module is the Python backend for ``uses: veridian/verify-action@v1``.
It reads configuration from environment variables (set by the GitHub Actions
runner from ``with:`` inputs), runs the configured verifier against the agent
output, writes a JSON result file, and exits non-zero on failure.

Environment variables (all optional with sensible defaults):

+------------------------------+-----------------------------------------+
| Variable                     | Description                             |
+==============================+=========================================+
| ``VERIDIAN_VERIFIER``        | Verifier ID (default: ``not_empty``)    |
| ``VERIDIAN_AGENT_OUTPUT``    | Agent output text to verify             |
| ``VERIDIAN_TASK``            | Task description (for verifier context) |
| ``VERIDIAN_OUTPUT_PATH``     | Path to write JSON result file          |
| ``VERIDIAN_FAIL_ON_ERROR``   | Exit 1 on failure (default: ``true``)   |
+------------------------------+-----------------------------------------+

Usage (as GitHub Action)::

    - uses: veridian/verify-action@v1
      with:
        verifier: schema
        agent-output: ${{ steps.agent.outputs.result }}
        task: "Summarise the quarterly report"

Usage (programmatic)::

    from veridian.gh_action import run_action
    result = run_action()
    assert result.passed
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veridian.core.task import Task, TaskResult
from veridian.verify.base import registry as verifier_registry

__all__ = ["ActionConfig", "ActionResult", "run_action"]

# Ensure built-in verifiers are registered
import veridian.verify.builtin as _builtin  # noqa: F401, E402

# ── ActionConfig ──────────────────────────────────────────────────────────────


@dataclass
class ActionConfig:
    """Configuration for one action run, read from environment variables."""

    verifier_id: str = "not_empty"
    agent_output: str = ""
    task_description: str = "Agent output verification"
    output_path: str = "veridian_action_result.json"
    fail_on_error: bool = True

    @classmethod
    def from_env(cls) -> ActionConfig:
        """Populate from environment variables."""
        fail_raw = os.environ.get("VERIDIAN_FAIL_ON_ERROR", "true").lower()
        return cls(
            verifier_id=os.environ.get("VERIDIAN_VERIFIER", "not_empty"),
            agent_output=os.environ.get("VERIDIAN_AGENT_OUTPUT", ""),
            task_description=os.environ.get("VERIDIAN_TASK", "Agent output verification"),
            output_path=os.environ.get("VERIDIAN_OUTPUT_PATH", "veridian_action_result.json"),
            fail_on_error=fail_raw not in ("0", "false", "no"),
        )


# ── ActionResult ──────────────────────────────────────────────────────────────


@dataclass
class ActionResult:
    """Result of a single action run."""

    passed: bool
    verifier_id: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "passed": self.passed,
            "verifier_id": self.verifier_id,
            "error": self.error,
        }


# ── run_action ────────────────────────────────────────────────────────────────


def run_action(config: ActionConfig | None = None) -> ActionResult:
    """
    Execute the verification action.

    Reads ``ActionConfig.from_env()`` if no config is provided, runs the
    configured verifier, writes the result JSON, and returns ``ActionResult``.
    """
    cfg = config or ActionConfig.from_env()

    # Build a minimal Task + TaskResult
    task = Task(
        title="CI/CD Verification",
        description=cfg.task_description,
        verifier_id=cfg.verifier_id,
    )
    task_result = TaskResult(raw_output=cfg.agent_output)

    # Resolve verifier
    try:
        verifier = verifier_registry.get(cfg.verifier_id)
    except Exception as exc:
        result = ActionResult(
            passed=False,
            verifier_id=cfg.verifier_id,
            error=f"Verifier not found: {exc}",
        )
        _write_result(cfg.output_path, result)
        return result

    # Run verification
    try:
        vr = verifier.verify(task, task_result)
        result = ActionResult(
            passed=vr.passed,
            verifier_id=cfg.verifier_id,
            error=vr.error,
        )
    except Exception as exc:
        result = ActionResult(
            passed=False,
            verifier_id=cfg.verifier_id,
            error=f"Verifier raised: {exc}",
        )

    _write_result(cfg.output_path, result)
    return result


def _write_result(path: str, result: ActionResult) -> None:
    """Atomically write the result JSON file."""
    import os
    import tempfile

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(result.to_dict(), indent=2)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".veridian_action_", suffix=".tmp")
    try:
        os.write(fd, data.encode())
        os.close(fd)
        os.replace(tmp, out)
    except Exception:
        import contextlib

        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the GitHub Action runner script."""
    result = run_action()
    if not result.passed:
        print(f"VERIFICATION FAILED: {result.error}", file=sys.stderr)
        if ActionConfig.from_env().fail_on_error:
            sys.exit(1)
    else:
        print(f"VERIFICATION PASSED (verifier={result.verifier_id})")


if __name__ == "__main__":
    main()
