"""Focused regressions for atomic writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veridian.core.atomic_io import atomic_write_json, atomic_write_text


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

