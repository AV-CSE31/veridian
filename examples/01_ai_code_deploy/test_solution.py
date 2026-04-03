"""
Tests for Problem 1: AI Code Deploy — reproducing Amazon outage patterns.
Uses Veridian's real ToolSafetyVerifier + SchemaVerifier.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent))
from solution import verify_deploy

# Real incident patterns parametrized
BLOCKED_CASES = [
    ("kiro_os_system", "import os\nos.system('kubectl apply -f x.yaml --force')", "Amazon Kiro: os.system deploy"),
    ("env_leak", "import os\nos.environ['PROD_DB_URL']", "Credential leak via os.environ"),
    ("eval_config", "config = eval(open('x.py').read())", "eval() on file — arbitrary execution"),
    ("pickle_load", "import pickle\npickle.loads(d)", "pickle deserialization — code exec vector"),
    ("shutil_import", "import shutil\nshutil.copy('a', 'b')", "shutil — filesystem manipulation"),
]

SCHEMA_FAILURES = [
    ("missing_both", "x = 1", {"done": True}, "Missing status + migration_complete"),
    ("missing_status", "x = 1", {"migration_complete": True}, "Missing status field"),
]

PASS_CASES = [
    ("json_safe", "import json\njson.loads('{}')", {"status": "ok", "migration_complete": True}),
    ("math_safe", "import math\nmath.sqrt(4)", {"status": "ok", "migration_complete": True}),
    ("dataclass_safe", "from dataclasses import dataclass\n@dataclass\nclass X:\n    v: int", {"status": "ok", "migration_complete": True}),
]


class TestBlocksAmazonOutagePatterns:
    @pytest.mark.parametrize("name,code,desc", BLOCKED_CASES)
    def test_gate1_blocks_unsafe_code(self, name: str, code: str, desc: str) -> None:
        s, r = verify_deploy(code, {"status": "ok", "migration_complete": True}, name)
        assert s == "BLOCKED", f"{name}: {desc} should be blocked"
        assert "Gate 1" in r

    @pytest.mark.parametrize("name,code,output,desc", SCHEMA_FAILURES)
    def test_gate2_blocks_missing_fields(self, name: str, code: str, output: dict[str, object], desc: str) -> None:
        s, r = verify_deploy(code, output, name)
        assert s == "BLOCKED", f"{name}: {desc} should be blocked"
        assert "Gate 2" in r


class TestPassesSafeDeployments:
    @pytest.mark.parametrize("name,code,output", PASS_CASES)
    def test_passes_safe_code(self, name: str, code: str, output: dict[str, object]) -> None:
        s, _ = verify_deploy(code, output, name)
        assert s == "DEPLOY"
