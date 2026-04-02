"""
Tests for Problem 8: Deleted Databases
=======================================
Each test case reproduces the EXACT code pattern from a documented incident.
Failure-path tests come first — they prove the incident cannot recur.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from solution import check


class TestBlocksDocumentedIncidents:
    """Every test here reproduces a real, documented AI data-destruction incident."""

    def test_blocks_replit_shutil_rmtree(self) -> None:
        """Replit Jul 2025: Agent deleted production DB during code freeze."""
        safe, err = check("import shutil\nshutil.rmtree('/var/lib/postgresql/data')")
        assert not safe, "Replit incident pattern must be blocked"
        assert err  # error message must explain why

    def test_blocks_claude_terraform_destroy(self) -> None:
        """Claude Code 2025: Terraform destroy wiped 2.5yr of data + backups."""
        safe, _ = check("import os\nos.system('terraform destroy -auto-approve')")
        assert not safe

    def test_blocks_claude_rm_rf_home(self) -> None:
        """Claude CLI 2025: rm -rf wiped entire home directory."""
        safe, _ = check("import os\nos.system('rm -rf /home/user/')")
        assert not safe

    def test_blocks_claude_cowork_photos(self) -> None:
        """Claude Cowork Jan 2026: Deleted 15 years of family photos."""
        safe, _ = check("import shutil\nshutil.rmtree('/Users/john/Family Photos')")
        assert not safe

    def test_blocks_kiro_aws_delete(self) -> None:
        """Amazon Kiro Dec 2025: Deleted live production environment."""
        safe, _ = check("import os\nos.system('aws rds delete-db-instance --db-instance-identifier prod')")
        assert not safe

    def test_blocks_eval_injection(self) -> None:
        safe, err = check("result = eval(user_input)")
        assert not safe
        assert "eval" in err.lower()

    def test_blocks_pickle_deserialization(self) -> None:
        safe, _ = check("import pickle\npickle.loads(data)")
        assert not safe

    def test_blocks_env_secret_access(self) -> None:
        safe, _ = check("import os\nkey = os.environ['SECRET']")
        assert not safe

    def test_blocks_socket_exfiltration(self) -> None:
        safe, _ = check("import socket\ns = socket.socket()")
        assert not safe


class TestPassesSafeCode:
    """Prove legitimate code is not blocked (zero false positives)."""

    def test_passes_json(self) -> None:
        assert check("import json\njson.loads('{}')")[0] is True

    def test_passes_math(self) -> None:
        assert check("import math\nmath.sqrt(4)")[0] is True

    def test_passes_dataclass(self) -> None:
        assert check("from dataclasses import dataclass\n@dataclass\nclass X:\n    v: int")[0] is True

    def test_passes_pathlib(self) -> None:
        assert check("from pathlib import Path\nPath('f.txt').read_text()")[0] is True

    def test_passes_empty(self) -> None:
        assert check("")[0] is True

    def test_passes_pure_computation(self) -> None:
        assert check("x = [s for s in [1,2,3] if s > 1]")[0] is True
