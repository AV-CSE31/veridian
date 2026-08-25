"""Repository diff guard for coding-agent completion gates."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.report import stable_hash
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    ".claude/*",
    "docs/*",
    "planning/*",
    "research/*",
)

DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    r"AKIA[0-9A-Z]{16}",
    r"(?i)\b(api[_-]?key|secret|token|password)\s*=\s*['\"]?[^'\"\s]+",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-[A-Za-z0-9_-]{16,}\b",
)

DEFAULT_IGNORED_PATHS: tuple[str, ...] = (
    ".coverage",
    ".pytest_cache/*",
    "__pycache__/*",
    "*/__pycache__/*",
    "*.pyc",
)


class RepoGuardVerifier(BaseVerifier):
    """Validate changed files before an agent claim is accepted."""

    id: ClassVar[str] = "repo_guard"
    description: ClassVar[str] = (
        "Verify git changes stay inside allowed paths, avoid protected paths, "
        "and do not introduce obvious secrets."
    )

    def __init__(
        self,
        repo_root: str = ".",
        allowed_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        ignored_paths: list[str] | None = None,
        secret_patterns: list[str] | None = None,
        require_changes: bool = False,
        max_scan_bytes: int = 1_000_000,
    ) -> None:
        root = Path(repo_root).resolve()
        if not root.exists() or not root.is_dir():
            raise VeridianConfigError(f"RepoGuardVerifier: repo_root does not exist: {repo_root}")
        if max_scan_bytes <= 0:
            raise VeridianConfigError("RepoGuardVerifier: max_scan_bytes must be > 0")
        self.repo_root = root
        self.allowed_paths = allowed_paths or []
        self.protected_paths = protected_paths or list(DEFAULT_PROTECTED_PATHS)
        self.ignored_paths = ignored_paths or list(DEFAULT_IGNORED_PATHS)
        self.secret_patterns = [
            re.compile(pattern) for pattern in (secret_patterns or list(DEFAULT_SECRET_PATTERNS))
        ]
        self.require_changes = require_changes
        self.max_scan_bytes = max_scan_bytes

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        changed = [
            path for path in self._changed_files() if not self._matches(path, self.ignored_paths)
        ]
        repo_state_digest = self._repo_state_digest(changed)
        base_evidence = {
            "changed_files": changed,
            "repo_state_digest": repo_state_digest,
        }
        if self.require_changes and not changed:
            return VerificationResult(
                passed=False,
                error="Repo guard failed: no changed files detected.",
                evidence=base_evidence,
            )

        protected = [path for path in changed if self._matches(path, self.protected_paths)]
        if protected:
            return VerificationResult(
                passed=False,
                error=f"Repo guard failed: protected path changed: {protected[0]}",
                evidence={**base_evidence, "protected_paths": protected},
            )

        outside_allowed = [
            path
            for path in changed
            if self.allowed_paths and not self._matches(path, self.allowed_paths)
        ]
        if outside_allowed:
            return VerificationResult(
                passed=False,
                error=f"Repo guard failed: changed file outside allowed paths: {outside_allowed[0]}",
                evidence={**base_evidence, "outside_allowed": outside_allowed},
            )

        secret_hits = self._scan_for_secrets(changed)
        if secret_hits:
            first = secret_hits[0]
            return VerificationResult(
                passed=False,
                error=f"Repo guard failed: possible secret introduced in {first['path']}",
                evidence={**base_evidence, "secret_hits": secret_hits},
            )

        return VerificationResult(
            passed=True,
            evidence={
                **base_evidence,
                "allowed_paths": self.allowed_paths,
                "protected_paths": self.protected_paths,
                "ignored_paths": self.ignored_paths,
            },
        )

    def _changed_files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "-C", str(self.repo_root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise VeridianConfigError(
                f"RepoGuardVerifier: git status failed for {self.repo_root}: {proc.stderr[:200]}"
            )
        changed: list[str] = []
        for line in proc.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            changed.append(path.replace("\\", "/"))
        return sorted(dict.fromkeys(changed))

    def _repo_state_digest(self, changed_files: list[str]) -> str:
        """Bind a verdict to the exact changed paths and worktree bytes observed."""
        entries: list[dict[str, str]] = []
        for relative in changed_files:
            path = self.repo_root / relative
            if path.is_symlink():
                entries.append(
                    {
                        "path": relative,
                        "kind": "symlink",
                        "content_hash": stable_hash(os.readlink(path)),
                    }
                )
                continue
            if not path.exists():
                entries.append({"path": relative, "kind": "missing", "content_hash": ""})
                continue
            if not path.is_file():
                entries.append({"path": relative, "kind": "other", "content_hash": ""})
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "content_hash": digest.hexdigest(),
                }
            )
        return f"sha256:{stable_hash(entries)}"

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

    def _scan_for_secrets(self, changed_files: list[str]) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        for relative in changed_files:
            path = (self.repo_root / relative).resolve()
            if not path.exists() or not path.is_file():
                continue
            try:
                data = path.read_bytes()[: self.max_scan_bytes]
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in self.secret_patterns:
                if pattern.search(text):
                    hits.append({"path": relative, "pattern": pattern.pattern})
                    break
        return hits
