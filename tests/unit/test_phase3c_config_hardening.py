"""
tests.unit.test_phase3c_config_hardening
────────────────────────────────────────
Acceptance tests for Phase 3.C config hardening:

* ``VeridianConfig.__post_init__`` rejects out-of-range values at
  construction so operator typos surface immediately instead of as
  confusing runtime errors deep in the runner.
* ``safe_report_path`` resolves operator-supplied report paths to the
  data dir and rejects paths that escape it (defence against
  ``report_path="../../etc/passwd"`` style misconfiguration).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from veridian.core.config import VeridianConfig, safe_report_path
from veridian.core.exceptions import VeridianConfigError

# ── Bounds validation ───────────────────────────────────────────────────────


class TestConfigBounds:
    def test_defaults_are_valid(self) -> None:
        # No exception — defaults must be self-consistent.
        VeridianConfig()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_parallel", 0),
            ("max_parallel", -1),
            ("max_cost_usd", 0.0),
            ("max_cost_usd", -10.0),
            ("max_tokens", 0),
            ("provider_timeout", 0),
            ("provider_timeout", -1),
            ("context_window_tokens", 0),
            ("ledger_lock_timeout", 0.0),
            ("drift_window", 0),
            ("skill_top_k", 0),
            ("max_turns_per_task", 0),
        ],
    )
    def test_rejects_non_positive(self, field: str, value: float) -> None:
        with pytest.raises(VeridianConfigError, match=field):
            VeridianConfig(**{field: value})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("compaction_threshold", 1.5),
            ("compaction_threshold", -0.1),
            ("drift_threshold", 1.5),
            ("skill_min_confidence", 1.5),
            ("evolution_safety_threshold", 2.0),
            ("evolution_refusal_baseline", -0.1),
            ("fingerprint_similarity_threshold", 1.01),
        ],
    )
    def test_rejects_out_of_unit_interval(self, field: str, value: float) -> None:
        with pytest.raises(VeridianConfigError, match=field):
            VeridianConfig(**{field: value})  # type: ignore[arg-type]

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(VeridianConfigError, match="temperature"):
            VeridianConfig(temperature=-0.5)

    def test_rejects_unknown_storage_backend(self) -> None:
        with pytest.raises(VeridianConfigError, match="storage_backend"):
            VeridianConfig(storage_backend="cassandra")

    def test_max_retries_zero_allowed(self) -> None:
        # ``max_retries=0`` is "no retries", which is valid.
        VeridianConfig(max_retries=0)


# ── safe_report_path ─────────────────────────────────────────────────────────


class TestSafeReportPath:
    def test_relative_path_anchored_to_data_dir(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(tmp_path)}, clear=False):
            resolved = safe_report_path("report.md")
        assert resolved == (tmp_path / "report.md").resolve()

    def test_nested_relative_path_anchored(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(tmp_path)}, clear=False):
            resolved = safe_report_path("subdir/report.md")
        assert resolved == (tmp_path / "subdir" / "report.md").resolve()

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(tmp_path)}, clear=False):
            with pytest.raises(VeridianConfigError, match="escapes data dir"):
                safe_report_path("../../etc/passwd")

    def test_absolute_outside_root_rejected(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(tmp_path)}, clear=False):
            with pytest.raises(VeridianConfigError, match="escapes data dir"):
                safe_report_path("/etc/passwd")

    def test_explicit_default_dir_used(self, tmp_path: Path) -> None:
        # An explicit default_dir overrides the env var.
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        resolved = safe_report_path("report.md", default_dir=explicit)
        assert resolved == (explicit / "report.md").resolve()
