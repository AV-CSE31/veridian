"""
tests.unit.test_phase4c_env_config
──────────────────────────────────
Acceptance tests for Phase 4.C — generic ``VERIDIAN_*`` env-var expansion.

* ``VeridianConfig.from_env`` reads ``VERIDIAN_<FIELD>`` for every field
  and coerces the value to the field's declared type.
* Explicit kwargs win over env vars; env vars win over dataclass defaults.
* Bool / int / float / Path / Optional fields all coerce correctly.
* Malformed values raise ``VeridianConfigError`` with the env key in the
  message so operators can locate the offending entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.exceptions import VeridianConfigError


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    # Wipe out any previously-set VERIDIAN_* so a single test sees only
    # what it set, then layer the new values on top.
    import os

    for key in list(os.environ):
        if key.startswith("VERIDIAN_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


class TestFromEnvCoercion:
    def test_int_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_PARALLEL="8")
        cfg = VeridianConfig.from_env()
        assert cfg.max_parallel == 8
        assert isinstance(cfg.max_parallel, int)

    def test_float_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_COST_USD="25.5")
        cfg = VeridianConfig.from_env()
        assert cfg.max_cost_usd == 25.5

    def test_bool_field_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for literal in ("1", "true", "yes", "on", "Y", "T", "TRUE"):
            _env(monkeypatch, VERIDIAN_DRY_RUN=literal)
            cfg = VeridianConfig.from_env()
            assert cfg.dry_run is True, f"{literal!r} should coerce to True"

    def test_bool_field_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for literal in ("0", "false", "no", "off", "N", "F", "FALSE"):
            _env(monkeypatch, VERIDIAN_DRY_RUN=literal)
            cfg = VeridianConfig.from_env()
            assert cfg.dry_run is False, f"{literal!r} should coerce to False"

    def test_invalid_bool_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_DRY_RUN="maybe")
        with pytest.raises(VeridianConfigError, match="not a valid boolean"):
            VeridianConfig.from_env()

    def test_path_field(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "ledger.json"
        _env(monkeypatch, VERIDIAN_LEDGER_FILE=str(target))
        cfg = VeridianConfig.from_env()
        assert cfg.ledger_file == target

    def test_optional_str_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_TRACE_FILE="/var/log/v.jsonl")
        cfg = VeridianConfig.from_env()
        assert cfg.trace_file == "/var/log/v.jsonl"

    def test_optional_field_cleared_with_none_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _env(monkeypatch, VERIDIAN_TRACE_FILE="none")
        cfg = VeridianConfig.from_env()
        assert cfg.trace_file is None

    def test_invalid_int_raises_with_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_PARALLEL="not-a-number")
        with pytest.raises(VeridianConfigError, match="VERIDIAN_MAX_PARALLEL"):
            VeridianConfig.from_env()


class TestFromEnvPrecedence:
    def test_explicit_kwargs_win_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_PARALLEL="4")
        cfg = VeridianConfig.from_env(max_parallel=12)
        assert cfg.max_parallel == 12

    def test_env_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_TURNS_PER_TASK="42")
        cfg = VeridianConfig.from_env()
        assert cfg.max_turns_per_task == 42

    def test_no_env_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch)  # clear all VERIDIAN_*
        cfg = VeridianConfig.from_env()
        # Sanity: defaults intact (compare against the field's documented default).
        assert cfg.max_parallel == 1
        assert cfg.dry_run is False

    def test_custom_prefix_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch)
        monkeypatch.setenv("MYAPP_MAX_PARALLEL", "16")
        cfg = VeridianConfig.from_env(prefix="MYAPP_")
        assert cfg.max_parallel == 16

    def test_explicit_env_dict_overrides_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_PARALLEL="2")
        cfg = VeridianConfig.from_env(env={"VERIDIAN_MAX_PARALLEL": "9"})
        assert cfg.max_parallel == 9


class TestFromEnvValidationStillApplies:
    def test_invalid_value_caught_by_post_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, VERIDIAN_MAX_PARALLEL="-1")
        # __post_init__ bounds checks from Phase 3.C still run.
        with pytest.raises(VeridianConfigError, match="max_parallel"):
            VeridianConfig.from_env()
