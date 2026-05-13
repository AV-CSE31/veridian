"""
tests.unit.test_phase6d_doc_coverage
────────────────────────────────────
Phase 6.D doc-coverage acceptance: every symbol exported via
``veridian.__all__`` must have a non-empty docstring. This catches
public-API regressions where a symbol is exported but not documented,
which would surface as cryptic ``help(veridian.X)`` output.

The test deliberately walks ``veridian.__all__`` rather than introspecting
the module directly so it tracks the documented surface as the public
contract evolves.
"""

from __future__ import annotations

import pytest


def _is_documented(obj: object) -> bool:
    """An object is considered documented when ``__doc__`` is a non-empty
    string after whitespace stripping. Module-level constants (which
    don't carry docstrings) are exempted via the caller's allowlist.
    """
    doc = getattr(obj, "__doc__", None)
    return bool(doc and doc.strip())


# Constants from primitive types don't have docstrings. The exemption
# list is intentionally short and explicitly documented so it can't
# silently grow.
_EXEMPT_CONSTANTS: frozenset[str] = frozenset(
    {
        "__version__",
        "THREAT_GAPS",  # MappingProxyType — see module docstring
    }
)


def _public_symbols() -> list[tuple[str, object]]:
    import veridian

    return [(name, getattr(veridian, name)) for name in veridian.__all__]


@pytest.mark.parametrize("name,obj", _public_symbols(), ids=lambda item: str(item))
def test_public_symbol_is_documented(name: str, obj: object) -> None:
    """Every name in ``veridian.__all__`` must carry a docstring.

    Exempt: well-known constants listed in ``_EXEMPT_CONSTANTS``.
    """
    if name in _EXEMPT_CONSTANTS:
        return
    assert _is_documented(obj), (
        f"veridian.{name} is exported via __all__ but has no docstring. "
        "Add a one-line summary so ``help(veridian.{name})`` is useful."
    )
