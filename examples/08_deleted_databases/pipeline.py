"""
Problem 8: The Deleted Database — Enterprise Code Safety Pipeline
==================================================================
Multi-layer analysis preventing AI agents from destroying production data.

INCIDENTS (Oct 2024 - Feb 2026, 10+ documented, 6 AI tools):
  Replit: Deleted production DB during code freeze, created 4K fake users
  Claude Code: Terraform destroy wiped 2.5yr database + backup snapshots
  Claude CLI: rm -rf on entire Mac home directory (family photos gone)
  Claude Cowork: "Organize desktop" deleted 15 years of family photos
  Amazon Kiro: Deleted live AWS production environment (13hr outage)

ARCHITECTURE:
  Layer 1: Veridian ToolSafetyVerifier (AST analysis — the real one)
  Layer 2: Threat Classifier (maps findings to categories + incidents)
  Layer 3: Security Report (human-readable with incident references)

USAGE:
    pip install veridian-ai
    cd examples/08_deleted_databases
    python pipeline.py                                          # Full demo
    python pipeline.py "import shutil; shutil.rmtree('/data')"  # Check code
    pytest test_pipeline.py -v                                  # Run tests
"""

from __future__ import annotations

import sys
import time
from typing import ClassVar

from analyzers.models import AnalysisReport
from analyzers.threat_classifier import ThreatClassifier
from data.incident_samples import INCIDENT_SAMPLES, SAFE_SAMPLES
from reporters.security_report import generate_report

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


class CodeSafetyPipelineVerifier(BaseVerifier):
    """Enterprise code safety — Veridian BaseVerifier integration.

    Wraps ToolSafetyVerifier + ThreatClassifier into a single verifier
    that returns threat-classified results with incident references.
    """

    id: ClassVar[str] = "code_safety_pipeline"
    description: ClassVar[str] = (
        "Enterprise code safety: AST analysis + threat classification + incident mapping"
    )

    def __init__(self) -> None:
        self._classifier = ThreatClassifier()

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        code = (result.structured or {}).get("code", "") or result.raw_output or ""
        report = self._classifier.analyze(code, code_id=task.id)

        if not report.blocked:
            return VerificationResult(passed=True, evidence=report.to_dict())

        # Build actionable error from threats
        top = report.threats[0] if report.threats else None
        error = (
            f"[{top.level.value.upper()}] {top.category.value}: {top.description}"
            if top
            else report.veridian_error
        )
        return VerificationResult(passed=False, error=error, evidence=report.to_dict())

    def analyze(self, code: str, code_id: str = "submission") -> AnalysisReport:
        """Direct analysis without Veridian Task/Result wrapping."""
        return self._classifier.analyze(code, code_id)


def main() -> None:
    start = time.monotonic()
    classifier = ThreatClassifier()

    if len(sys.argv) > 1:
        # Single code check
        code = " ".join(sys.argv[1:])
        report = classifier.analyze(code, "cli_input")
        print(generate_report(report))
        sys.exit(1 if report.blocked else 0)

    # Full demo — process all incident samples + safe samples
    print("\n" + "=" * 75)
    print("  VERIDIAN — Enterprise Code Safety Pipeline")
    print("  Reproducing 5 real incidents + 4 attack patterns + 5 safe samples")
    print("  Layer 1: ToolSafetyVerifier (AST)  |  Layer 2: Threat Classifier")
    print("=" * 75)

    blocked = passed = missed = fps = 0

    print("\n  INCIDENT REPRODUCTIONS (must ALL be blocked)")
    print("  " + "-" * 71)
    for sample_id, code, desc, _should_block in INCIDENT_SAMPLES:
        report = classifier.analyze(code, sample_id)
        if report.blocked:
            level = report.max_threat_level.value.upper()
            print(f"  BLOCKED  [{level:8s}] {sample_id}")
            print(f"           {desc[:70]}")
            if report.threats:
                print(f"           Prevents: {report.threats[0].incident_ref[:65]}")
            blocked += 1
        else:
            print(f"  !! MISS  {sample_id}: {desc[:60]}")
            missed += 1

    print("\n  SAFE CODE (must ALL pass)")
    print("  " + "-" * 71)
    for sample_id, code, desc, _should_block in SAFE_SAMPLES:
        report = classifier.analyze(code, sample_id)
        if not report.blocked:
            print(f"  PASSED   {sample_id}: {desc[:60]}")
            passed += 1
        else:
            print(f"  !! FP    {sample_id}: blocked safe code")
            fps += 1

    elapsed = int((time.monotonic() - start) * 1000)

    print(f"\n  {'=' * 71}")
    print(f"  Incidents blocked: {blocked}/{len(INCIDENT_SAMPLES)}")
    print(f"  Safe code passed:  {passed}/{len(SAFE_SAMPLES)}")
    print(f"  Missed: {missed}  |  False positives: {fps}  |  {elapsed}ms")
    if missed == 0 and fps == 0:
        print("  VERDICT: All incidents blocked. Zero false positives.")
        print("  The 15 years of family photos would still exist.")
    print(f"  {'=' * 71}")

    # Print detailed report for the most severe incident
    print("\n  --- Detailed Report: Replit Database Deletion ---")
    replit = classifier.analyze(INCIDENT_SAMPLES[0][1], "replit_detailed")
    print(generate_report(replit))


if __name__ == "__main__":
    main()
