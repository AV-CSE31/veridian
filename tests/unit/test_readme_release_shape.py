from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_describes_the_v03_slim_runtime() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "The `0.3.0` release is intentionally light" in readme
    assert "The base install is small and only requires `filelock`." in readme
    assert "examples/decorator_release_gate.py" in readme


def test_readme_does_not_advertise_removed_platform_surfaces() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    removed_claims = [
        "veridian init",
        "dashboard endpoints",
        "Redis storage backend",
        "Postgres storage backend",
        "Certified adapters",
        "semantic_grounding",
        "llm_judge",
        "tool_safety",
        "`confidence`: require",
    ]
    for claim in removed_claims:
        assert claim not in readme
