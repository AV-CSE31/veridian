"""Config hardening tests for the slim runtime."""

from __future__ import annotations

import pytest

from chit.core.config import ChitConfig
from chit.core.exceptions import ChitConfigError


class TestConfigBounds:
    def test_defaults_are_valid(self) -> None:
        config = ChitConfig()

        assert config.report_file is None
        assert config.report_signing_key is None

    def test_durable_reporting_requires_operator_key_material(self, tmp_path) -> None:
        with pytest.raises(ChitConfigError, match="report_signing_key"):
            ChitConfig(report_file=tmp_path / "reports.jsonl")

        with pytest.raises(ChitConfigError, match="at least 32 bytes"):
            ChitConfig(
                report_file=tmp_path / "reports.jsonl",
                report_signing_key="weak-key",
            )

    def test_report_key_can_be_loaded_from_environment_without_repr_leak(self, tmp_path) -> None:
        key = "environment-report-signing-material-32"
        config = ChitConfig.from_env(
            env={
                "CHIT_REPORT_FILE": str(tmp_path / "reports.jsonl"),
                "CHIT_REPORT_SIGNING_KEY": key,
                "CHIT_REPORT_INCLUDE_PAYLOADS": "true",
            }
        )

        assert config.report_signing_key == key
        assert config.report_include_payloads is True
        assert key not in repr(config)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_cost_usd", 0.0),
            ("max_cost_usd", -10.0),
            ("max_tokens", 0),
            ("provider_timeout", 0),
            ("provider_timeout", -1),
            ("context_window_tokens", 0),
            ("ledger_lock_timeout", 0.0),
            ("max_turns_per_task", 0),
        ],
    )
    def test_rejects_non_positive(self, field: str, value: float) -> None:
        with pytest.raises(ChitConfigError, match=field):
            ChitConfig(**{field: value})  # type: ignore[arg-type]

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(ChitConfigError, match="temperature"):
            ChitConfig(temperature=-0.5)

    def test_max_retries_zero_allowed(self) -> None:
        ChitConfig(max_retries=0)

    def test_dashboard_config_knob_stays_removed(self) -> None:
        with pytest.raises(TypeError, match="dashboard_port"):
            ChitConfig(dashboard_port=7474)  # type: ignore[call-arg]

    def test_parallel_runner_config_knob_stays_removed(self) -> None:
        with pytest.raises(TypeError, match="max_parallel"):
            ChitConfig(max_parallel=2)  # type: ignore[call-arg]

    def test_context_compactor_config_knob_stays_removed(self) -> None:
        with pytest.raises(TypeError, match="compaction_threshold"):
            ChitConfig(compaction_threshold=0.85)  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "field",
        [
            "drift_history_file",
            "drift_window",
            "drift_threshold",
            "evolution_monitor_file",
            "evolution_safety_threshold",
            "evolution_refusal_baseline",
            "fingerprint_history_file",
            "fingerprint_similarity_threshold",
            "canary_suite_path",
        ],
    )
    def test_research_hook_config_knobs_stay_removed(self, field: str) -> None:
        with pytest.raises(TypeError, match=field):
            ChitConfig(**{field: "removed"})  # type: ignore[arg-type]
