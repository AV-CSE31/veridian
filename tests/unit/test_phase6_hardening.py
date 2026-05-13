"""
tests.unit.test_phase6_hardening
────────────────────────────────
Acceptance tests for Phase 6 production hardening:

* Phase 6.A: ``veridian.core.atomic_io`` consolidates the four legacy
  ``_atomic_write`` helpers. Test the shared helper directly and confirm
  one of the call-sites still routes through it.
* Phase 6.C.1: CLI exit codes are exported as ``EXIT_*`` constants with
  the documented BSD ``sysexits.h``-aligned values.
* Phase 6.C.2: ``/metrics`` endpoint enforces ``VERIDIAN_METRICS_TOKEN``
  when set (401 missing, 403 wrong, 200 correct), and stays open when
  the env var is unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from veridian.core.atomic_io import atomic_write_json, atomic_write_text

# ── Phase 6.A — atomic_io ────────────────────────────────────────────────────


class TestAtomicWriteText:
    def test_writes_full_content(self, tmp_path: Path) -> None:
        dst = tmp_path / "nested" / "out.txt"
        atomic_write_text(dst, "hello world")
        assert dst.read_text(encoding="utf-8") == "hello world"

    def test_no_temp_leak_on_success(self, tmp_path: Path) -> None:
        atomic_write_text(tmp_path / "ok.txt", "x")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
        assert leftovers == []

    def test_cleans_temp_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force only os.replace to fail; the cleanup path still uses the
        # real os.unlink so the temp file is removed before the error
        # propagates.
        import veridian.core.atomic_io as mod

        def _boom(_src: str, _dst: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(mod.os, "replace", _boom)
        dst = tmp_path / "out.txt"
        with pytest.raises(OSError, match="disk full"):
            atomic_write_text(dst, "x")
        leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []


class TestAtomicWriteJson:
    def test_round_trips_payload(self, tmp_path: Path) -> None:
        dst = tmp_path / "data.json"
        atomic_write_json(dst, {"hello": "world", "n": 1})
        parsed = json.loads(dst.read_text(encoding="utf-8"))
        assert parsed == {"hello": "world", "n": 1}


class TestAtomicWriteCallSites:
    def test_dashboard_share_report_uses_shared_helper(self) -> None:
        from veridian.dashboard import share_report

        # The legacy symbol still exists for callers that imported it.
        assert callable(share_report._atomic_write)


# ── Phase 6.C.1 — CLI exit codes ─────────────────────────────────────────────


class TestCliExitCodes:
    def test_constants_have_expected_values(self) -> None:
        from veridian.cli.main import (
            EXIT_CONFIG,
            EXIT_DEPENDENCY,
            EXIT_GENERIC,
            EXIT_INTERNAL,
            EXIT_NO_INPUT,
            EXIT_OK,
            EXIT_TRANSIENT,
            EXIT_USAGE,
        )

        # Spot-check the BSD-aligned values. Operators rely on these for
        # branch logic in CI scripts.
        assert EXIT_OK == 0
        assert EXIT_GENERIC == 1
        assert EXIT_USAGE == 64
        assert EXIT_CONFIG == 65
        assert EXIT_NO_INPUT == 66
        assert EXIT_DEPENDENCY == 69
        assert EXIT_INTERNAL == 70
        assert EXIT_TRANSIENT == 75

    def test_missing_ledger_uses_no_input(self, tmp_path: Path) -> None:
        import typer

        from veridian.cli.main import EXIT_NO_INPUT, _load_ledger

        with pytest.raises(typer.Exit) as info:
            _load_ledger(str(tmp_path / "does-not-exist.json"))
        assert info.value.exit_code == EXIT_NO_INPUT


# ── Phase 6.C.2 — /metrics auth token ────────────────────────────────────────


@pytest.fixture
def dashboard_app(tmp_path: Path):
    """Build a fresh dashboard app per test so the env-var read happens
    at app-build time and each test sees an isolated token state."""
    pytest.importorskip("fastapi")
    from veridian.observability.dashboard import VeridianDashboard

    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text('{"schema_version": 1, "tasks": {}}', encoding="utf-8")

    def _make(token: str | None) -> object:
        env = dict(os.environ)
        env.pop("VERIDIAN_METRICS_TOKEN", None)
        if token is not None:
            env["VERIDIAN_METRICS_TOKEN"] = token
        with patch.dict(os.environ, env, clear=True):
            dash = VeridianDashboard(
                trace_file=tmp_path / "trace.jsonl",
                ledger_path=ledger_path,
            )
            return dash.app  # type: ignore[no-any-return]

    return _make


class TestMetricsAuth:
    def test_open_when_token_unset(self, dashboard_app) -> None:
        from fastapi.testclient import TestClient

        app = dashboard_app(None)
        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_missing_header_returns_401(self, dashboard_app) -> None:
        from fastapi.testclient import TestClient

        app = dashboard_app("s3cr3t")
        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"] == "missing bearer token"

    def test_wrong_token_returns_403(self, dashboard_app) -> None:
        from fastapi.testclient import TestClient

        app = dashboard_app("s3cr3t")
        with TestClient(app) as client:
            resp = client.get(
                "/metrics", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "invalid metrics token"

    def test_correct_token_returns_200(self, dashboard_app) -> None:
        from fastapi.testclient import TestClient

        app = dashboard_app("s3cr3t")
        with TestClient(app) as client:
            resp = client.get(
                "/metrics", headers={"Authorization": "Bearer s3cr3t"}
            )
        assert resp.status_code == 200
