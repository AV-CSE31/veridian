"""Anti-sprawl tests for the slim package boundary."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import tomllib
from dataclasses import fields
from importlib import import_module
from pathlib import Path

import veridian
from veridian.core.config import VeridianConfig

ROOT = Path(__file__).resolve().parents[2]

_EXPECTED_TOP_LEVEL_PACKAGES = {
    "adapters",
    "assurance",
    "banking",
    "context",
    "core",
    "effects",
    # The gate porcelain composes assurance + effects into the documented
    # front door. It adds no new trust primitives; it removes ceremony.
    "gate",
    "hooks",
    "ledger",
    "loop",
    "math",
    "providers",
    "verify",
}


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_top_level_package_set_is_pinned() -> None:
    actual = {
        path.name
        for path in (ROOT / "veridian").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert actual == _EXPECTED_TOP_LEVEL_PACKAGES


def test_core_package_stays_runtime_focused() -> None:
    allowed = {
        "__init__.py",
        "atomic_io.py",
        "contract.py",
        "config.py",
        "events.py",
        "exceptions.py",
        "report.py",
        "task.py",
    }
    actual = {path.name for path in (ROOT / "veridian" / "core").glob("*.py")}
    assert actual == allowed


def test_context_package_stays_prompt_focused() -> None:
    allowed = {"__init__.py", "manager.py", "window.py"}
    actual = {path.name for path in (ROOT / "veridian" / "context").glob("*.py")}
    assert actual == allowed


def test_verify_package_stays_runtime_focused() -> None:
    allowed = {"__init__.py", "base.py"}
    actual = {path.name for path in (ROOT / "veridian" / "verify").glob("*.py")}
    assert actual == allowed


def test_public_api_stays_small() -> None:
    # Raised 24 -> 33 when the gate porcelain was promoted to the top level.
    # The assurance kernel was 54% of the library and reachable only by
    # submodule path; `import veridian` showed callers the task runner and
    # hid the differentiator. Growth beyond this stays deliberate.
    assert len(veridian.__all__) <= 33


def test_removed_root_extras_stay_removed() -> None:
    removed = {
        "benchmark.py",
        "budget.py",
        "cost.py",
        "decorator.py",
        "gh_action.py",
    }
    existing = {path.name for path in (ROOT / "veridian").glob("*.py")}
    assert removed.isdisjoint(existing)


def test_deleted_platform_exceptions_stay_removed() -> None:
    removed = {
        "AgentIdentityNotFound",
        "AuditIntegrityError",
        "CanaryRegressionError",
        "CheckpointError",
        "ComplianceError",
        "ComplianceGapError",
        "DashboardError",
        "GraphError",
        "KeyRotationError",
        "KnowledgeGraphError",
        "NLPolicyError",
        "OperatorError",
        "PKIError",
        "PluginError",
        "PolicyCompilationError",
        "PolicyError",
        "PolicyNotFound",
        "SagaError",
        "SelfImprovingError",
        "SignatureVerificationError",
        "PipelineError",
        "VerifierIntegrityError",
    }
    exceptions = import_module("veridian.core.exceptions")
    assert all(not hasattr(exceptions, name) for name in removed)


def test_base_dependency_count_stays_bounded() -> None:
    dependencies = _pyproject()["project"]["dependencies"]
    assert len(dependencies) <= 5


def test_storage_backend_config_knob_stays_removed() -> None:
    config_fields = {field.name for field in fields(VeridianConfig)}
    assert "storage_backend" not in config_fields


def test_skill_memory_config_knobs_stay_removed() -> None:
    config_fields = {field.name for field in fields(VeridianConfig)}
    assert "skill_library_path" not in config_fields
    assert "skill_min_confidence" not in config_fields
    assert "skill_max_retries" not in config_fields
    assert "skill_top_k" not in config_fields


def test_secret_provider_config_knobs_stay_removed() -> None:
    config_fields = {field.name for field in fields(VeridianConfig)}
    assert "secrets_env_prefix" not in config_fields
    assert "identity_guard_enabled" not in config_fields


def test_dashboard_config_knob_stays_removed() -> None:
    config_fields = {field.name for field in fields(VeridianConfig)}
    assert "dashboard_port" not in config_fields


def test_parallel_runner_config_knob_stays_removed() -> None:
    config_fields = {field.name for field in fields(VeridianConfig)}
    assert "max_parallel" not in config_fields


def test_optional_dependency_roots_not_imported_by_base_import() -> None:
    code = textwrap.dedent(
        """
        import json
        import sys

        before = set(sys.modules)
        import veridian  # noqa: F401
        loaded = sorted(
            root
            for root in {name.split('.')[0] for name in set(sys.modules) - before}
            if root in {
                'cryptography',
                'litellm',
                'networkx',
                'pypdf',
                'tenacity',
                'tiktoken',
            }
        )
        print(json.dumps(loaded))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "[]"


def test_removed_preview_adapter_modules_stay_removed() -> None:
    integrations_dir = ROOT / "veridian" / "integrations"
    assert not integrations_dir.exists()


def test_builtin_hooks_are_runtime_only() -> None:
    allowed = {
        "__init__.py",
        "cost_guard.py",
        "human_review.py",
        "logging_hook.py",
        "rate_limit.py",
    }
    actual = {path.name for path in (ROOT / "veridian" / "hooks" / "builtin").glob("*.py")}
    assert actual == allowed


def test_builtin_verifier_package_stays_practical() -> None:
    allowed = {
        "__init__.py",
        "any_of.py",
        "bash.py",
        "composite.py",
        "confidence.py",
        "file_exists.py",
        "http.py",
        "quote.py",
        "repo_guard.py",
        "schema.py",
    }
    existing = {path.name for path in (ROOT / "veridian" / "verify" / "builtin").glob("*.py")}
    assert existing == allowed


def test_loop_package_stays_runtime_focused() -> None:
    removed = {
        "activity_boundary.py",
        "activity.py",
        "checkpoint_cursor.py",
        "parallel_runner.py",
        "scheduler.py",
        "trusted_executor.py",
    }
    existing = {path.name for path in (ROOT / "veridian" / "loop").glob("*.py")}
    assert removed.isdisjoint(existing)


def test_removed_agent_variant_modules_stay_removed() -> None:
    assert not (ROOT / "veridian" / "agents").exists()


def test_deleted_platform_packages_stay_removed() -> None:
    removed = {
        "audit",
        "agents",
        "cli",
        "compliance",
        "contracts",
        "dashboard",
        "entropy",
        "eval",
        "explain",
        "graph",
        "identity",
        "intelligence",
        "knowledge",
        "mcp",
        "observability",
        "operator",
        "plugins",
        "policy",
        "protocols",
        "secrets",
        "skills",
        "storage",
        "testing",
    }
    existing = {
        path.name
        for path in (ROOT / "veridian").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert removed.isdisjoint(existing)


def test_examples_stay_small_and_release_focused() -> None:
    example_files = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "examples").rglob("*")
        if path.is_file()
    }
    assert example_files == {
        "examples/artifact_verification_gate.py",
        "examples/banking_agent_verification_demo.py",
        "examples/coding_agent_verification_demo.py",
        "examples/decorator_release_gate.py",
        "examples/gate_quickstart.py",
        "examples/runner_release_gate.py",
    }
