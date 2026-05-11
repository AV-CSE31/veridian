from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_GUIDES = ROOT / "guides" / "production"


def _read(name: str) -> str:
    return (PRODUCTION_GUIDES / name).read_text(encoding="utf-8")


def test_production_guide_set_exists() -> None:
    required = {
        "README.md",
        "verification-contract.md",
        "evidence-timeline.md",
        "trusted-executor.md",
        "threat-model.md",
        "roadmap-v0.5.md",
    }
    missing = [name for name in sorted(required) if not (PRODUCTION_GUIDES / name).exists()]
    assert not missing


def test_verification_contract_preserves_completion_boundary() -> None:
    text = _read("verification-contract.md")
    assert "must not transition to `DONE`" in text
    assert "independent verifier" in text
    assert "Silent verifier failures are not production-supported." in text


def test_evidence_timeline_names_minimum_operator_fields() -> None:
    text = _read("evidence-timeline.md")
    for field in [
        "`run_id`",
        "`task_id`",
        "`verifier_id`",
        "`input_hash`",
        "`output_hash`",
        "`passed`",
        "`error`",
    ]:
        assert field in text


def test_trusted_executor_page_does_not_claim_sandboxing() -> None:
    text = _read("trusted-executor.md")
    assert "not a complete sandbox boundary" in text
    assert "does not claim to provide" in text
    assert "container isolation" in text


def test_v05_roadmap_keeps_certified_claims_narrow() -> None:
    text = _read("roadmap-v0.5.md")
    assert "LangGraph and CrewAI as the only certified adapters" in text
    assert "Pydantic AI durable-execution sidecar spike" in text
    assert "Mastra sidecar protocol spike" in text
    assert "OpenAI Agents SDK guardrail bridge" in text
    assert "Inspect AI evidence export" in text
