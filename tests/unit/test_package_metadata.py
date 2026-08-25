from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_extra_references_published_package_name() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = pyproject["project"]["optional-dependencies"]
    assert optional["all"] == ["veridian-ai[http,llm,pdf,pydantic]"]


def test_base_dependencies_include_proof_verification_but_not_optional_integrations() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(pyproject["project"]["dependencies"])
    names = {item.split(">=", 1)[0] for item in dependencies}
    assert "cryptography" in names
    assert "networkx" not in names
    assert "pypdf" not in names
    assert "httpx" not in names
    assert "pydantic" not in names
    assert "tiktoken" not in names
    assert "tenacity" not in names


def test_verifier_entry_points_match_lazy_registry() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["veridian.verifiers"]

    from veridian.verify.base import registry

    assert entry_points == registry._lazy
