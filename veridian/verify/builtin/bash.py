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

import os
import subprocess
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "PWD",
    "TMPDIR",
)

DEFAULT_BLOCKLIST: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf ~",
    "sudo rm",
    ":(){ :|:& };:",
    "> /dev/sda",
    "mkfs",
    "dd if=/dev/zero",
    "chmod 777 /",
    "wget http",
    "curl http://",
)


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
        blocklist: list[str] | None = None,
        env_allowlist: tuple[str, ...] | None = None,
        inherit_env: bool = False,
    ) -> None:
        """
        Args:
            command: Shell command to execute. Must be non-empty.
            expected_exit: Expected exit code. Default 0 (success).
            timeout_seconds: Maximum execution time. Must be > 0.
            blocklist: Substrings that, if present in the command, cause the
                verifier to reject it as misconfigured.
            env_allowlist: Environment variables passed to the child process.
                Parent env is NOT inherited by default, preventing accidental
                leakage of credentials into shell commands.
            inherit_env: Set to ``True`` to inherit the full parent env. Only
                use in trusted, non-adversarial contexts.
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
        self.blocklist = list(blocklist) if blocklist is not None else list(DEFAULT_BLOCKLIST)
        normalised = " ".join(command.lower().split())
        for blocked in self.blocklist:
            if blocked.lower() in normalised:
                raise VeridianConfigError(
                    f"BashExitCodeVerifier: command rejected by blocklist "
                    f"(matched {blocked!r}). Adjust the command or pass a "
                    "custom `blocklist=` to override."
                )
        self.command = command
        self.expected_exit = expected_exit
        self.timeout_seconds = timeout_seconds
        self.env_allowlist = (
            env_allowlist if env_allowlist is not None else DEFAULT_ENV_ALLOWLIST
        )
        self.inherit_env = inherit_env

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run self.command in a subprocess and check its exit code."""
        if self.inherit_env:
            child_env: dict[str, str] | None = None
        else:
            child_env = {
                key: os.environ[key] for key in self.env_allowlist if key in os.environ
            }
        try:
            proc = subprocess.run(  # noqa: S602  (blocklist + env scrub above)
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=child_env,
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
