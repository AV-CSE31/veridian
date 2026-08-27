#!/usr/bin/env python3
"""Fail loudly when the newest git tag and the published PyPI version diverge.

Between April and August 2026 this repository advanced three minor versions
while PyPI still served 0.1.0, and nothing reported it. This script is the
missing signal: it is cheap enough to run on a schedule and unambiguous enough
that a divergence cannot be mistaken for a passing build.

Exit codes:
    0  parity, or a deliberate skip (no tags yet)
    1  divergence, or PyPI cannot be reached
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
DEFAULT_PACKAGE = "veridian-ai"
TIMEOUT_SECONDS = 30


def newest_tag() -> str | None:
    """Return the newest ``vX.Y.Z`` tag by version order, or None if untagged."""
    try:
        completed = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: cannot read git tags: {exc}", file=sys.stderr)
        return None
    tags = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return tags[0] if tags else None


def published_version(package: str) -> str | None:
    """Return the version PyPI currently serves as latest, or None on failure."""
    url = PYPI_JSON_URL.format(package=package)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"error: {package} is not published on PyPI", file=sys.stderr)
            return None
        print(f"error: PyPI returned HTTP {exc.code}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"error: cannot reach PyPI: {exc}", file=sys.stderr)
        return None
    version = payload.get("info", {}).get("version")
    return str(version) if version else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    arguments = parser.parse_args(argv)

    tag = newest_tag()
    if tag is None:
        print("skip: no version tags found; nothing to compare")
        return 0
    tag_version = tag.removeprefix("v")

    pypi_version = published_version(arguments.package)
    if pypi_version is None:
        return 1

    print(f"newest tag:      {tag} (version {tag_version})")
    print(f"pypi latest:     {pypi_version}")

    if tag_version == pypi_version:
        print("PARITY: the newest tag matches the published version.")
        return 0

    print(
        "\nDIVERGENCE: the newest tag and the published version differ.\n"
        f"  Tagged but unpublished: {tag_version}\n"
        f"  Serving to installers:  {pypi_version}\n"
        "\nAnyone running `pip install` receives the published version, not the\n"
        "tagged source. Either publish the tag or explain the gap in CHANGELOG.md.\n"
        "See RELEASING.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
