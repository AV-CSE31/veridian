#!/usr/bin/env python3
"""Validate release archives do not contain protected local material."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

PROTECTED_PARTS = {
    ".claude",
    "docs",
    "guides",
    "planning",
    "research",
}
PROTECTED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "ONBOARDING.md",
    "GEMINI.md",
    "ARCHITECTURE.md",
    "CODEBASE_HEALTH.md",
    "SESSION_HANDOFF.md",
}


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.suffixes[-2:] == [".tar", ".gz"] or path.suffix == ".tgz":
        with tarfile.open(path) as archive:
            return archive.getnames()
    return []


def _is_protected(name: str) -> bool:
    parts = Path(name).parts
    return any(part in PROTECTED_PARTS for part in parts) or any(
        part in PROTECTED_FILES for part in parts
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+")
    args = parser.parse_args()

    violations: list[str] = []
    for raw in args.archives:
        path = Path(raw)
        for name in _archive_names(path):
            if _is_protected(name):
                violations.append(f"{path}: {name}")

    if violations:
        print("Protected paths found in release artifacts:")
        for item in violations:
            print(f" - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
