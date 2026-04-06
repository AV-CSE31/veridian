"""
Tests for Problem 8: Deleted Databases — Enterprise Code Safety Pipeline.
Each test reproduces the EXACT code pattern from a documented incident.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from analyzers.models import ThreatLevel
from analyzers.threat_classifier import ThreatClassifier
from data.incident_samples import INCIDENT_SAMPLES, SAFE_SAMPLES


def _load_local_module(filename: str, alias: str) -> object:
    module_path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


# Avoid cross-example namespace collisions when examples are run in one pytest call.
for stale in (
    "reporters",
    "reporters.audit_report",
    "reporters.security_report",
    "analyzers",
    "data",
    "pipeline",
):
    sys.modules.pop(stale, None)

_pipeline = _load_local_module("pipeline.py", f"{Path(__file__).parent.name}_pipeline")
CodeSafetyPipelineVerifier = _pipeline.CodeSafetyPipelineVerifier

from veridian.core.task import Task, TaskResult


@pytest.fixture
def classifier() -> ThreatClassifier:
    return ThreatClassifier()


@pytest.fixture
def verifier() -> CodeSafetyPipelineVerifier:
    return CodeSafetyPipelineVerifier()


class TestBlocksAllDocumentedIncidents:
    """Every test reproduces a real, documented data-destruction incident."""

    @pytest.mark.parametrize("sample_id,code,desc,should_block", INCIDENT_SAMPLES)
    def test_blocks_incident(
        self, classifier: ThreatClassifier, sample_id: str, code: str, desc: str, should_block: bool
    ) -> None:
        report = classifier.analyze(code, sample_id)
        assert report.blocked, f"Incident '{sample_id}' should be blocked: {desc}"

    def test_replit_classified_as_critical(self, classifier: ThreatClassifier) -> None:
        report = classifier.analyze("import shutil\nshutil.rmtree('/data')", "replit")
        assert report.max_threat_level == ThreatLevel.CRITICAL

    def test_eval_classified_as_high(self, classifier: ThreatClassifier) -> None:
        report = classifier.analyze("eval(user_input)", "eval")
        assert report.max_threat_level in (ThreatLevel.CRITICAL, ThreatLevel.HIGH)

    def test_findings_include_incident_references(self, classifier: ThreatClassifier) -> None:
        report = classifier.analyze("import shutil\nshutil.rmtree('/data')", "ref_check")
        assert any(t.incident_ref for t in report.threats), "Findings must reference real incidents"


class TestPassesAllSafeCode:
    """Prove legitimate code is not blocked (zero false positives)."""

    @pytest.mark.parametrize("sample_id,code,desc,should_block", SAFE_SAMPLES)
    def test_passes_safe_code(
        self, classifier: ThreatClassifier, sample_id: str, code: str, desc: str, should_block: bool
    ) -> None:
        report = classifier.analyze(code, sample_id)
        assert not report.blocked, f"Safe code '{sample_id}' should not be blocked: {desc}"


class TestVeridianIntegration:
    """Prove the pipeline works as a Veridian BaseVerifier."""

    def test_verifier_blocks_dangerous_code(self, verifier: CodeSafetyPipelineVerifier) -> None:
        task = Task(id="t1", title="check", verifier_id="code_safety_pipeline")
        result = TaskResult(raw_output="import shutil\nshutil.rmtree('/data')")
        v = verifier.verify(task, result)
        assert v.passed is False
        assert v.error

    def test_verifier_passes_safe_code(self, verifier: CodeSafetyPipelineVerifier) -> None:
        task = Task(id="t2", title="check", verifier_id="code_safety_pipeline")
        result = TaskResult(raw_output="import json\njson.loads('{}')")
        v = verifier.verify(task, result)
        assert v.passed is True

    def test_verifier_evidence_includes_report(self, verifier: CodeSafetyPipelineVerifier) -> None:
        task = Task(id="t3", title="check", verifier_id="code_safety_pipeline")
        result = TaskResult(raw_output="eval(x)")
        v = verifier.verify(task, result)
        assert "threats_found" in v.evidence
