"""
tests.unit.test_phase1c_operability
───────────────────────────────────
Acceptance tests for Phase 1.C container/operability fixes:

* ``default_data_dir()`` resolves ``VERIDIAN_DATA_DIR`` with mkdir, falling
  back to PWD when the env var is unset — keeping bare-PWD scripts working
  while letting containers persist on a mounted volume.
* ``VeridianConfig`` exposes ``ledger_lock_timeout`` and feeds it through
  to ``TaskLedger`` (covered indirectly by the SDK).
* The dashboard ``/ready`` endpoint returns 503 when persistence is not
  addressable and 200 once the verifier registry is loaded and the ledger
  file exists.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from veridian.core.config import VeridianConfig, default_data_dir

# ── default_data_dir ─────────────────────────────────────────────────────────


class TestDefaultDataDir:
    def test_env_var_creates_and_returns_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "varlib" / "veridian"
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(target)}, clear=False):
            resolved = default_data_dir()
        assert resolved == target
        assert resolved.exists() and resolved.is_dir()

    def test_no_env_var_falls_back_to_cwd(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        env.pop("VERIDIAN_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            cwd_before = Path.cwd()
            assert default_data_dir() == cwd_before

    def test_config_defaults_anchor_paths_to_data_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "anchored"
        with patch.dict(os.environ, {"VERIDIAN_DATA_DIR": str(target)}, clear=False):
            cfg = VeridianConfig()
        assert cfg.ledger_file.parent == target
        assert cfg.progress_file.parent == target


# ── ledger_lock_timeout knob ────────────────────────────────────────────────


class TestLedgerLockTimeoutKnob:
    def test_config_exposes_default(self) -> None:
        cfg = VeridianConfig()
        assert cfg.ledger_lock_timeout == 15.0

    def test_sdk_passes_timeout_through_to_ledger(self, tmp_path: Path) -> None:
        from veridian.integrations.sdk import start_run
        from veridian.providers.mock_provider import MockProvider

        config = VeridianConfig(
            ledger_file=tmp_path / "ledger.json",
            progress_file=tmp_path / "progress.md",
            ledger_lock_timeout=2.5,
        )
        provider = MockProvider()
        ctx = start_run(config=config, provider=provider)
        # FileLock instances stash the timeout; assert the value made it.
        assert ctx.ledger._lock.timeout == pytest.approx(2.5)


# ── Dashboard /ready endpoint ────────────────────────────────────────────────


@pytest.fixture
def app(tmp_path: Path):
    fastapi = pytest.importorskip("fastapi")
    from veridian.observability.dashboard import VeridianDashboard

    # Touch a ledger file so the readiness check on the path succeeds.
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text('{"schema_version": 1, "tasks": {}}', encoding="utf-8")
    dash = VeridianDashboard(
        trace_file=tmp_path / "trace.jsonl",
        ledger_path=ledger_path,
    )
    return dash.app  # type: ignore[no-any-return]


class TestReadyEndpoint:
    def test_returns_ready_when_dependencies_present(self, app, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Ensure built-in verifiers are registered so the registry probe passes.
        import veridian.verify.builtin  # noqa: F401

        with TestClient(app) as client:
            resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}

    def test_returns_503_when_ledger_missing(self, tmp_path: Path) -> None:
        fastapi = pytest.importorskip("fastapi")  # noqa: F841
        from fastapi.testclient import TestClient

        from veridian.observability.dashboard import VeridianDashboard

        missing = tmp_path / "does-not-exist.json"
        dash = VeridianDashboard(trace_file=tmp_path / "trace.jsonl", ledger_path=missing)
        with TestClient(dash.app) as client:
            resp = client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert "not_ready" in body["detail"]
        reasons = {c["check"] for c in body["detail"]["not_ready"]}
        assert "ledger" in reasons

    def test_health_remains_shallow(self, app) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
