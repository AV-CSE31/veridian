from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_sdist_excludes_protected_paths() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(pyproject["tool"]["hatch"]["build"]["exclude"])

    assert "/guides" in excluded
    assert "/docs" in excluded
    assert "/planning" in excluded
    assert "/research" in excluded
    assert "/.claude" in excluded


def test_package_artifact_checker_exists_for_release_workflow() -> None:
    assert (ROOT / "scripts" / "check_package_artifacts.py").exists()
