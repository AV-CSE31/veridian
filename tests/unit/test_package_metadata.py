from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_extra_references_published_package_name() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = pyproject["project"]["optional-dependencies"]
    assert optional["all"] == ["veridian-ai[http,llm,pdf,pydantic]"]


def test_heavy_dependencies_are_not_required_by_base_install() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(pyproject["project"]["dependencies"])
    names = {item.split(">=", 1)[0] for item in dependencies}
    assert "networkx" not in names
    assert "cryptography" not in names
    assert "pypdf" not in names
    assert "httpx" not in names
    assert "pydantic" not in names
    assert "tiktoken" not in names
    assert "tenacity" not in names
