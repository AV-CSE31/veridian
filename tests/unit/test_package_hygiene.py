"""
tests.unit.test_package_hygiene
────────────────────────────────
F1 from the 2026-04-06 cleanup audit: prevent the recurrence of duplicate
``x/x`` nested package trees that shipped by accident as
``veridian/explain/explain/`` and ``veridian/intelligence/intelligence/``.

This test walks the installed package directory and fails if any child
subpackage repeats its parent's name. It also flags a handful of modules
the audit marked as orphan risk so they cannot re-enter the release
artifact without an explicit review.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "veridian"


def _iter_subpackages(root: Path):
    """Yield every directory under ``root`` that is a Python package
    (contains ``__init__.py``)."""
    if not root.is_dir():
        return
    for path in root.rglob("__init__.py"):
        yield path.parent


class TestNoDuplicatePackageNesting:
    def test_no_child_package_repeats_parent_name(self) -> None:
        """``veridian/foo/foo/`` pattern is forbidden.

        The 2026-04-06 audit found two such duplicates
        (``explain/explain``, ``intelligence/intelligence``) that shipped
        in the wheel at 0% coverage. This assertion keeps them out.
        """
        offenders: list[str] = []
        for pkg_dir in _iter_subpackages(_PACKAGE_ROOT):
            if pkg_dir == _PACKAGE_ROOT:
                continue
            parent = pkg_dir.parent
            if parent == _PACKAGE_ROOT:
                continue
            if pkg_dir.name == parent.name:
                offenders.append(str(pkg_dir.relative_to(_PACKAGE_ROOT)))
        assert not offenders, (
            "Duplicate nested package trees detected — remove them:\n"
            + "\n".join(f"  - veridian/{p}" for p in offenders)
        )

    def test_deleted_duplicate_trees_stay_deleted(self) -> None:
        """Explicit belt-and-braces check for the two specific dirs that
        the audit called out. Guards against accidental restore-from-backup."""
        banned = [
            _PACKAGE_ROOT / "explain" / "explain",
            _PACKAGE_ROOT / "intelligence" / "intelligence",
        ]
        present = [str(p.relative_to(_PACKAGE_ROOT.parent)) for p in banned if p.exists()]
        assert not present, (
            "The following duplicate package trees must stay deleted "
            "per planning/08-code-cleanup-and-competitive-audit-2026-04-06.md "
            f"section A: {present}"
        )
