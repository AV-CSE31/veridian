"""Doc-coverage acceptance for the slim public API."""

from __future__ import annotations

import pytest


def _is_documented(obj: object) -> bool:
    """Return True when an exported object has a useful docstring."""
    doc = getattr(obj, "__doc__", None)
    return bool(doc and doc.strip())


_EXEMPT_CONSTANTS: frozenset[str] = frozenset({"__version__"})


def _public_symbols() -> list[tuple[str, object]]:
    import veridian

    return [(name, getattr(veridian, name)) for name in veridian.__all__]


@pytest.mark.parametrize("name,obj", _public_symbols(), ids=lambda item: str(item))
def test_public_symbol_is_documented(name: str, obj: object) -> None:
    """Every name in ``veridian.__all__`` must carry a docstring."""
    if name in _EXEMPT_CONSTANTS:
        return
    assert _is_documented(obj), (
        f"veridian.{name} is exported via __all__ but has no docstring. "
        f"Add a one-line summary so help(veridian.{name}) is useful."
    )
