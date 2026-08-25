"""Release-surface contracts for the CLI, composite Action, and container."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_composite_action_installs_and_runs_its_checked_out_source() -> None:
    action = (ROOT / ".github" / "actions" / "verify" / "action.yml").read_text(encoding="utf-8")

    assert "pip install veridian-ai" not in action
    assert "python -m pip install" in action
    assert '"${{ github.action_path }}/../../.."' in action
    assert "veridian verify" in action
    assert "veridian.gh_action" not in action
    assert "verifier-config:" in action


def test_container_entrypoint_resolves_to_the_supported_console_script() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert project["project"]["scripts"]["veridian"] == "veridian.cli:main"
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "veridian"]' in dockerfile
    assert 'CMD ["--help"]' in dockerfile
    assert "ParallelRunner" not in dockerfile
    assert 'if [ -n "${VERIDIAN_EXTRAS}" ]' in dockerfile


def test_security_policy_uses_private_reporting_and_states_trust_boundaries() -> None:
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "security/advisories/new" in policy
    assert "ships no fallback secret" in policy
    assert "not an OS security" in policy and "sandbox" in policy
    assert "single-host" in policy
