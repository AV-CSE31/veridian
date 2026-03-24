"""
veridian.verify.builtin.bash
────────────────────────────
BashExitCodeVerifier — runs a shell command and checks its exit code.

The command is specified in verifier_config per task, e.g.:
    verifier_id="bash_exit"
    verifier_config={"command": "pytest tests/test_auth.py -v"}

The verifier runs the command independently (not checking agent bash_outputs).
This provides a deterministic, tamper-proof verification that is completely
separate from anything the agent may have executed.
"""
from __future__ import annotations

import subprocess
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


class BashExitCodeVerifier(BaseVerifier):
    """
    Run a shell command and pass if its exit code matches expected_exit.

    Stateless: all config is in constructor. Safe for concurrent use.
    """

    id: ClassVar[str] = "bash_exit"
    description: ClassVar[str] = (
        "Run a shell command and verify its exit code. "
        "Pass only when exit_code == expected_exit (default 0)."
    )

    def __init__(
        self,
        command: str,
        expected_exit: int = 0,
        timeout_seconds: int = 60,
    ) -> None:
        """
        Args:
            command: Shell command to execute. Must be non-empty.
            expected_exit: Expected exit code. Default 0 (success).
            timeout_seconds: Maximum execution time. Must be > 0.
        """
        if not command or not command.strip():
            raise VeridianConfigError(
                "BashExitCodeVerifier: 'command' must not be empty. "
                "Provide a shell command string, e.g. 'pytest tests/'."
            )
        if timeout_seconds <= 0:
            raise VeridianConfigError(
                f"BashExitCodeVerifier: 'timeout_seconds' must be > 0, got {timeout_seconds}."
            )
        self.command = command
        self.expected_exit = expected_exit
        self.timeout_seconds = timeout_seconds

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run self.command in a subprocess and check its exit code."""
        try:
            proc = subprocess.run(  # noqa: S602  (shell=True is intentional for flexibility)
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return VerificationResult(
                passed=False,
                error=(
                    f"Command '{self.command[:60]}' timed out after {self.timeout_seconds}s. "
                    f"Reduce scope or increase timeout_seconds."
                )[:300],
            )

        actual = proc.returncode
        if actual == self.expected_exit:
            return VerificationResult(
                passed=True,
                evidence={
                    "exit_code": actual,
                    "command": self.command,
                    "stdout_tail": proc.stdout[-200:] if proc.stdout else "",
                },
            )

        # Build actionable error within 300 chars
        stdout_tail = (proc.stdout or "").strip()[-150:]
        stderr_tail = (proc.stderr or "").strip()[-100:]
        error = (
            f"Command '{self.command[:50]}' exited {actual} (expected {self.expected_exit}). "
            f"stdout: {stdout_tail} stderr: {stderr_tail}"
        )[:300]

        return VerificationResult(
            passed=False,
            error=error,
            evidence={
                "exit_code": actual,
                "expected_exit": self.expected_exit,
                "command": self.command,
                "stdout": proc.stdout[-500:] if proc.stdout else "",
                "stderr": proc.stderr[-500:] if proc.stderr else "",
            },
        )
