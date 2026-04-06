"""
veridian.verify.builtin.file_exists
─────────────────────────────────────
FileExistsVerifier — verify that expected output files exist on disk.

Usage:
    verifier_id="file_exists"
    verifier_config={
        "files": ["output/report.json", "output/summary.md"],
        "check_non_empty": True,
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


class FileExistsVerifier(BaseVerifier):
    """
    Check that all files in the configured list exist (and are non-empty
    when check_non_empty=True).

    Stateless: all config is in constructor.
    """

    id: ClassVar[str] = "file_exists"
    description: ClassVar[str] = (
        "Verify that all expected output files exist on disk. "
        "Optionally require files to be non-empty."
    )

    def __init__(
        self,
        files: list[str] | None = None,
        check_non_empty: bool = True,
    ) -> None:
        """
        Args:
            files: List of file paths that must exist. Must be non-empty.
            check_non_empty: If True, each file must have size > 0. Default True.
        """
        if not files:
            raise VeridianConfigError(
                "FileExistsVerifier: 'files' must be a non-empty list of file paths. "
                "Provide at least one path, e.g. files=['output/report.json']."
            )
        self.files: list[str] = list(files)
        self.check_non_empty = check_non_empty

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Check each configured file exists and (if required) is non-empty."""
        for filepath in self.files:
            path = Path(filepath)

            if not path.exists():
                return VerificationResult(
                    passed=False,
                    error=(
                        f"File not found: {filepath}. "
                        f"Ensure the file was created at the expected path."
                    )[:300],
                    evidence={"missing_file": filepath},
                )

            if self.check_non_empty and path.stat().st_size == 0:
                return VerificationResult(
                    passed=False,
                    error=(
                        f"File exists but is empty: {filepath}. "
                        f"Write the expected content to the file."
                    )[:300],
                    evidence={"empty_file": filepath},
                )

        return VerificationResult(
            passed=True,
            evidence={
                "files_checked": self.files,
                "check_non_empty": self.check_non_empty,
            },
        )
