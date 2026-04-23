#!/usr/bin/env python3
"""Block accidental commits/pushes of confidential project paths."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable

PROTECTED_PREFIXES = (
    "planning/",
    "research/",
    "docs/",
    ".claude/",
)

PROTECTED_EXACT = {
    "AGENTS.md",
    "CLAUDE.md",
    "ONBOARDING.md",
    "CHANGELOG.md",
    "ARCHITECTURE.md",
    "CODEBASE_HEALTH.md",
    "SESSION_HANDOFF.md",
    "GEMINI.md",
}


def _run_git(args: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _is_protected(path: str) -> bool:
    if path in PROTECTED_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def _find_protected(paths: Iterable[str]) -> list[str]:
    return sorted(path for path in paths if _is_protected(path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check protected files are never pushed.")
    parser.add_argument(
        "--mode",
        choices=("staged", "tracked", "both"),
        default="both",
        help="What to inspect: staged changes, tracked files, or both.",
    )
    args = parser.parse_args()

    violations: list[str] = []

    if args.mode in {"staged", "both"}:
        staged = _run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"])
        violations.extend(_find_protected(staged))

    if args.mode in {"tracked", "both"}:
        tracked = _run_git(["ls-files"])
        violations.extend(_find_protected(tracked))

    violations = sorted(set(violations))
    if not violations:
        return 0

    print("ERROR: Protected IP paths detected in git tracking/staging:", file=sys.stderr)
    for path in violations:
        print(f" - {path}", file=sys.stderr)
    print(
        "Fix: unstage/remove from index and keep these paths local-only.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
