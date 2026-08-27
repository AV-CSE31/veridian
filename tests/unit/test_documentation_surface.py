"""Guards on the public documentation surface.

The library went five months with a single README as its entire public
documentation while the differentiating half of the code was reachable only by
submodule path. These tests keep the front door pointing at the right thing and
keep the honesty claims from quietly eroding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_PUBLIC_DOCS = {
    "README.md",
    "quickstart.md",
    "threat-model.md",
    "proof-format.md",
    "mapping-eu-ai-act-article-12.md",
    "mapping-open-agent-passport.md",
}


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class TestPublicDocsExist:
    @pytest.mark.parametrize("name", sorted(_PUBLIC_DOCS))
    def test_public_document_is_present(self, name: str) -> None:
        assert (ROOT / "docs" / name).is_file()

    def test_docs_tree_matches_the_gitignore_allowlist(self) -> None:
        """Every file under docs/ must be individually allowlisted."""
        present = {path.name for path in (ROOT / "docs").glob("*") if path.is_file()}
        ignore = _read(".gitignore")
        for name in present:
            assert f"!docs/{name}" in ignore, (
                f"docs/{name} exists but is not allowlisted in .gitignore. "
                "Add it deliberately or move it out of the public tree."
            )

    def test_root_contributor_files_exist(self) -> None:
        for name in ("CONTRIBUTING.md", "CHANGELOG.md", "RELEASING.md", "SECURITY.md"):
            assert (ROOT / name).is_file()


class TestReadmeLeadsWithTheAssuranceSurface:
    def test_quick_start_demonstrates_the_gate(self) -> None:
        readme = _read("README.md")
        quick_start = readme.split("## Quick Start", 1)[1].split("## Documentation", 1)[0]
        assert "Gate.for_development" in quick_start
        assert "@gate.guard" in quick_start
        assert "@check" in quick_start

    def test_quick_start_precedes_the_task_runner(self) -> None:
        readme = _read("README.md")
        assert readme.index("## Quick Start") < readme.index("## Task Runner")

    def test_readme_links_the_public_docs(self) -> None:
        readme = _read("README.md")
        for name in sorted(_PUBLIC_DOCS - {"README.md"}):
            assert f"docs/{name}" in readme

    def test_readme_flags_development_keys_as_unsafe(self) -> None:
        readme = _read("README.md")
        assert "ephemeral key" in readme


class TestHonestyClaimsSurvive:
    def test_threat_model_states_what_veridian_cannot_protect_against(self) -> None:
        threat = _read("docs", "threat-model.md")
        assert "## What Veridian cannot protect against" in threat
        assert "not an OS security sandbox" in threat
        for actor in ("compromised signer", "compromised trusted executor"):
            assert actor.lower() in threat.lower()

    def test_proof_format_refuses_to_overclaim_verification(self) -> None:
        proof = _read("docs", "proof-format.md")
        assert "not-checked" in proof
        assert "unanchored" in proof
        assert "A chain that vouches for itself vouches for nothing." in proof

    def test_article_12_mapping_disclaims_compliance(self) -> None:
        mapping = _read("docs", "mapping-eu-ai-act-article-12.md")
        assert "does not claim that using Veridian makes a system compliant" in mapping
        assert "## Where Veridian gives you nothing" in mapping
        assert "it is not a compliance product" in mapping

    def test_oap_mapping_is_marked_as_analysis_not_implementation(self) -> None:
        mapping = _read("docs", "mapping-open-agent-passport.md")
        assert "analysis, not implementation" in mapping
        assert "has not been validated clause-by-clause" in mapping


class TestChangelogRecordsTheDistributionGap:
    def test_changelog_states_which_versions_actually_reached_pypi(self) -> None:
        changelog = _read("CHANGELOG.md")
        assert "Distribution history" in changelog
        assert "0.1.0" in changelog and "0.4.0" in changelog
        assert "publish workflow failed" in changelog

    def test_releasing_documents_oidc_not_an_api_token(self) -> None:
        releasing = _read("RELEASING.md")
        assert "trusted publishing" in releasing
        assert "must not be reintroduced" in releasing
        assert "testpypi" in releasing

    def test_publish_workflow_has_no_api_token_credential(self) -> None:
        """The workflow authenticates by OIDC; a password input would be a regression."""
        workflow = _read(".github", "workflows", "publish.yml")
        assert "id-token: write" in workflow
        # The prose comment naming the retired secret is fine; a live reference
        # to it, or any password input, is the regression worth catching.
        assert "secrets.PYPI_API_TOKEN" not in workflow
        assert "password:" not in workflow
        assert "test.pypi.org/legacy/" in workflow

    def test_a_scheduled_job_watches_for_tag_versus_pypi_divergence(self) -> None:
        workflow = _read(".github", "workflows", "release-parity.yml")
        assert "schedule:" in workflow
        assert "check_release_parity.py" in workflow
