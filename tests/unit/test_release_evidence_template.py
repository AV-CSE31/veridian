from __future__ import annotations

from pathlib import Path


def test_release_evidence_template_exists_with_required_sections() -> None:
    template = Path(".github/RELEASE_EVIDENCE_TEMPLATE.md")
    assert template.exists(), "Missing .github/RELEASE_EVIDENCE_TEMPLATE.md"

    content = template.read_text(encoding="utf-8")
    required_sections = [
        "## Release Evidence Block",
        "### Metadata",
        "### Quality Gates",
        "### Test Summary",
        "### Packaging Evidence",
        "### Claim-to-Test Mapping",
        "### Sign-off",
    ]
    for section in required_sections:
        assert section in content, f"Missing section in release evidence template: {section}"


def test_releasing_guide_references_evidence_template() -> None:
    guide = Path("RELEASING.md")
    assert guide.exists(), "Missing RELEASING.md"

    content = guide.read_text(encoding="utf-8")
    assert ".github/RELEASE_EVIDENCE_TEMPLATE.md" in content
    assert "PyPI" in content
