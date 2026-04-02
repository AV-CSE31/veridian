"""Tests for Problem 1: AI Code Deploy — reproducing Amazon outage patterns."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from solution import verify_deploy


class TestBlocksAmazonOutagePatterns:
    def test_blocks_os_system_deploy(self) -> None:
        s, _ = verify_deploy("import os\nos.system('kubectl apply -f x.yaml')", {"status": "ok", "migration_complete": True})
        assert s == "BLOCKED"

    def test_blocks_eval_config(self) -> None:
        s, _ = verify_deploy("eval(open('x.py').read())", {"status": "ok", "migration_complete": True})
        assert s == "BLOCKED"

    def test_blocks_env_credential_leak(self) -> None:
        s, _ = verify_deploy("import os\nos.environ['SECRET']", {"status": "ok", "migration_complete": True})
        assert s == "BLOCKED"

    def test_blocks_pickle(self) -> None:
        s, _ = verify_deploy("import pickle\npickle.loads(d)", {"status": "ok", "migration_complete": True})
        assert s == "BLOCKED"

    def test_blocks_missing_schema_fields(self) -> None:
        s, r = verify_deploy("x = 1", {"done": True})
        assert s == "BLOCKED"
        assert "Gate 2" in r

class TestPassesSafeDeployments:
    def test_passes_json_migration(self) -> None:
        s, _ = verify_deploy("import json\njson.loads('{}')", {"status": "ok", "migration_complete": True})
        assert s == "DEPLOY"

    def test_passes_dataclass(self) -> None:
        s, _ = verify_deploy("from dataclasses import dataclass\n@dataclass\nclass X:\n    v: int",
                             {"status": "ok", "migration_complete": True})
        assert s == "DEPLOY"
