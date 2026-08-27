from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Confidential trees that must never appear in a wheel or sdist. `docs` is on
# this list even though part of it is now public: keeping every artifact free of
# docs/ lets scripts/check_package_artifacts.py stay an absolute rule rather
# than a split one that could let a private draft through.
_PROTECTED_TREES = ("docs", "guides", "planning", "research", ".claude")


def _exclude_patterns(table: dict[str, object]) -> set[str]:
    return {str(item) for item in table.get("exclude", [])}  # type: ignore[union-attr]


def _covers(patterns: set[str], tree: str) -> bool:
    """Whether any pattern excludes the whole tree.

    A bare ``/docs`` does *not* exclude the tree once .gitignore un-ignores
    files inside it — hatchling still collects them. Only a recursive glob does,
    so the accepted forms are pinned here rather than assumed.
    """
    return any(
        pattern in (f"/{tree}/**", f"{tree}/**", f"/{tree}/*", f"{tree}/*") for pattern in patterns
    )


def test_shared_build_config_excludes_protected_paths() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = _exclude_patterns(pyproject["tool"]["hatch"]["build"])

    for tree in _PROTECTED_TREES:
        assert _covers(patterns, tree), (
            f"{tree}/ is not recursively excluded from build artifacts. "
            f"A bare '/{tree}' entry is not sufficient — use '/{tree}/**'."
        )


def test_sdist_target_excludes_protected_paths() -> None:
    """The sdist needs its own exclude list; it does not inherit reliably."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]
    patterns = _exclude_patterns(sdist)

    for tree in _PROTECTED_TREES:
        assert _covers(patterns, tree), f"{tree}/ is not recursively excluded from the sdist"


def test_protected_root_files_are_excluded() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = _exclude_patterns(pyproject["tool"]["hatch"]["build"])

    for name in ("AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "SESSION_HANDOFF.md"):
        assert f"/{name}" in patterns


def test_package_artifact_checker_exists_for_release_workflow() -> None:
    assert (ROOT / "scripts" / "check_package_artifacts.py").exists()


def test_release_parity_checker_exists_for_release_workflow() -> None:
    assert (ROOT / "scripts" / "check_release_parity.py").exists()
