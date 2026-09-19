"""
tests.unit.test_phase4c_env_config
------------------------------------------------------------------------------------------------------
Acceptance tests for Phase 4.C --- generic ``CHIT_*`` env-var expansion.

* ``ChitConfig.from_env`` reads ``CHIT_<FIELD>`` for every field
  and coerces the value to the field's declared type.
* Explicit kwargs win over env vars; env vars win over dataclass defaults.
* Bool / int / float / Path / Optional fields all coerce correctly.
* Malformed values raise ``ChitConfigError`` with the env key in the
  message so operators can locate the offending entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chit.core.config import ChitConfig
from chit.core.exceptions import ChitConfigError


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    # Wipe out any previously-set CHIT_* so a single test sees only
    # what it set, then layer the new values on top.
    import os

    for key in list(os.environ):
        if key.startswith("CHIT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


class TestFromEnvCoercion:
    def test_int_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TOKENS="8192")
        cfg = ChitConfig.from_env()
        assert cfg.max_tokens == 8192
        assert isinstance(cfg.max_tokens, int)

    def test_float_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_COST_USD="25.5")
        cfg = ChitConfig.from_env()
        assert cfg.max_cost_usd == 25.5

    def test_bool_field_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for literal in ("1", "true", "yes", "on", "Y", "T", "TRUE"):
            _env(monkeypatch, CHIT_DRY_RUN=literal)
            cfg = ChitConfig.from_env()
            assert cfg.dry_run is True, f"{literal!r} should coerce to True"

    def test_bool_field_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for literal in ("0", "false", "no", "off", "N", "F", "FALSE"):
            _env(monkeypatch, CHIT_DRY_RUN=literal)
            cfg = ChitConfig.from_env()
            assert cfg.dry_run is False, f"{literal!r} should coerce to False"

    def test_invalid_bool_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_DRY_RUN="maybe")
        with pytest.raises(ChitConfigError, match="not a valid boolean"):
            ChitConfig.from_env()

    def test_path_field(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "ledger.json"
        _env(monkeypatch, CHIT_LEDGER_FILE=str(target))
        cfg = ChitConfig.from_env()
        assert cfg.ledger_file == target

    def test_invalid_int_raises_with_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TOKENS="not-a-number")
        with pytest.raises(ChitConfigError, match="CHIT_MAX_TOKENS"):
            ChitConfig.from_env()


class TestFromEnvPrecedence:
    def test_explicit_kwargs_win_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TOKENS="4096")
        cfg = ChitConfig.from_env(max_tokens=12000)
        assert cfg.max_tokens == 12000

    def test_env_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TURNS_PER_TASK="42")
        cfg = ChitConfig.from_env()
        assert cfg.max_turns_per_task == 42

    def test_no_env_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch)  # clear all CHIT_*
        cfg = ChitConfig.from_env()
        # Sanity: defaults intact (compare against the field's documented default).
        assert cfg.max_tokens == 4096
        assert cfg.dry_run is False

    def test_custom_prefix_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch)
        monkeypatch.setenv("MYAPP_MAX_TOKENS", "16000")
        cfg = ChitConfig.from_env(prefix="MYAPP_")
        assert cfg.max_tokens == 16000

    def test_explicit_env_dict_overrides_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TOKENS="2000")
        cfg = ChitConfig.from_env(env={"CHIT_MAX_TOKENS": "9000"})
        assert cfg.max_tokens == 9000


class TestFromEnvValidationStillApplies:
    def test_invalid_value_caught_by_post_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, CHIT_MAX_TOKENS="-1")
        # __post_init__ bounds checks from Phase 3.C still run.
        with pytest.raises(ChitConfigError, match="max_tokens"):
            ChitConfig.from_env()
