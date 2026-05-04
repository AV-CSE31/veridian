from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDES = ROOT / "guides" / "integrations"


def test_public_integration_guides_exist() -> None:
    required = {
        "README.md",
        "langgraph.md",
        "crewai.md",
        "openai-agents-sdk.md",
        "pydantic-ai.md",
        "mastra.md",
        "production-checklist.md",
    }
    missing = [name for name in sorted(required) if not (GUIDES / name).exists()]
    assert not missing


def test_certified_adapter_claims_match_existing_test_evidence() -> None:
    text = (GUIDES / "README.md").read_text(encoding="utf-8")
    assert "LangGraph | Certified adapter" in text
    assert "CrewAI | Certified adapter" in text

    for test_path in [
        ROOT / "tests" / "integration" / "test_langgraph_adapter.py",
        ROOT / "tests" / "integration" / "test_langgraph_certification.py",
        ROOT / "tests" / "integration" / "test_crewai_adapter.py",
        ROOT / "tests" / "integration" / "test_crewai_certification.py",
        ROOT / "tests" / "integration" / "test_certification_matrix.py",
    ]:
        assert test_path.exists()


def test_uncertified_frameworks_are_labeled_as_universal_patterns() -> None:
    readme = (GUIDES / "README.md").read_text(encoding="utf-8")
    assert "OpenAI Agents SDK | Universal verification pattern" in readme
    assert "Pydantic AI | Universal verification pattern" in readme
    assert "Mastra | Universal sidecar pattern" in readme

    for name in ["openai-agents-sdk.md", "pydantic-ai.md", "mastra.md"]:
        page = (GUIDES / name).read_text(encoding="utf-8")
        assert "There is no first-class" in page


def test_production_checklist_names_trusted_executor_boundary() -> None:
    page = (GUIDES / "production-checklist.md").read_text(encoding="utf-8")
    assert "TrustedExecutor" in page
    assert "not a complete sandbox boundary" in page

