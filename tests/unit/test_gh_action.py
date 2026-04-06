"""
tests/unit/test_gh_action.py
──────────────────────────────
Tests for A5: GitHub Action entrypoint (veridian/gh_action.py).

The action is invoked by the YAML workflow and runs verification
against agent outputs provided via environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from veridian.gh_action import ActionConfig, ActionResult, run_action


class TestActionConfig:
    def test_from_env_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            cfg = ActionConfig.from_env()
        assert cfg.verifier_id is not None

    def test_from_env_custom_verifier(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_VERIFIER": "schema"}):
            cfg = ActionConfig.from_env()
        assert cfg.verifier_id == "schema"

    def test_from_env_output_path(self, tmp_path: Path) -> None:
        out = str(tmp_path / "output.json")
        with patch.dict(
            os.environ,
            {"VERIDIAN_AGENT_OUTPUT": '{"summary": "done"}', "VERIDIAN_OUTPUT_PATH": out},
        ):
            cfg = ActionConfig.from_env()
        assert cfg.agent_output == '{"summary": "done"}'
        assert cfg.output_path == out

    def test_from_env_task_description(self) -> None:
        with patch.dict(os.environ, {"VERIDIAN_TASK": "Summarise the document."}):
            cfg = ActionConfig.from_env()
        assert cfg.task_description == "Summarise the document."


class TestRunAction:
    def test_passes_for_valid_output(self, tmp_path: Path) -> None:
        output_path = str(tmp_path / "result.json")
        env = {
            "VERIDIAN_VERIFIER": "not_empty",
            "VERIDIAN_AGENT_OUTPUT": "The analysis is complete.",
            "VERIDIAN_TASK": "Analyse the document.",
            "VERIDIAN_OUTPUT_PATH": output_path,
        }
        with patch.dict(os.environ, env):
            result = run_action()
        assert result.passed is True
        assert result.verifier_id == "not_empty"

    def test_fails_for_empty_output(self, tmp_path: Path) -> None:
        output_path = str(tmp_path / "result.json")
        env = {
            "VERIDIAN_VERIFIER": "not_empty",
            "VERIDIAN_AGENT_OUTPUT": "",
            "VERIDIAN_TASK": "Analyse the document.",
            "VERIDIAN_OUTPUT_PATH": output_path,
        }
        with patch.dict(os.environ, env):
            result = run_action()
        assert result.passed is False

    def test_writes_output_json(self, tmp_path: Path) -> None:
        output_path = str(tmp_path / "result.json")
        env = {
            "VERIDIAN_VERIFIER": "not_empty",
            "VERIDIAN_AGENT_OUTPUT": "some output",
            "VERIDIAN_TASK": "Do something.",
            "VERIDIAN_OUTPUT_PATH": output_path,
        }
        with patch.dict(os.environ, env):
            run_action()
        assert Path(output_path).exists()
        data = json.loads(Path(output_path).read_text())
        assert "passed" in data

    def test_result_has_verifier_id(self, tmp_path: Path) -> None:
        env = {
            "VERIDIAN_VERIFIER": "not_none",
            "VERIDIAN_AGENT_OUTPUT": "done",
            "VERIDIAN_OUTPUT_PATH": str(tmp_path / "r.json"),
        }
        with patch.dict(os.environ, env):
            result = run_action()
        assert result.verifier_id == "not_none"

    def test_action_result_to_dict(self) -> None:
        r = ActionResult(passed=True, verifier_id="schema", error=None)
        d = r.to_dict()
        assert d["passed"] is True
        assert d["verifier_id"] == "schema"
