from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_describes_the_v04_assurance_runtime_truthfully() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "The `0.4.0` source line remains alpha software" in readme
    assert "requires `cryptography`, `filelock`, and `jsonschema`" in readme
    assert "examples/decorator_release_gate.py" in readme
    assert "examples/coding_agent_verification_demo.py" in readme
    assert "examples/banking_agent_verification_demo.py" in readme
    assert "benchmarks/sota_assurance_bench.py" in readme
    assert "WAL is the default task-ledger mode" in readme
    assert "## Current Boundaries" in readme


def test_readme_documents_only_runnable_delivery_surfaces() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "veridian verify" in readme
    assert "veridian verify-receipt" in readme
    assert "AV-CSE31/veridian/.github/actions/verify@<commit-sha>" in readme
    assert "docker run --rm veridian:local verify" in readme
    assert "Repository source and published release artifacts can differ" in readme


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
