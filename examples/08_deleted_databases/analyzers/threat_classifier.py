"""
Threat Classifier — maps AST violations to incident-referenced threat findings.

Wraps Veridian's real ToolSafetyVerifier and enriches its output with:
  - Threat severity classification (CRITICAL → SAFE)
  - Threat category (data_destruction, code_execution, etc.)
  - Real-world incident references for each finding
  - Line-level code snippets
"""

from __future__ import annotations

import ast

from analyzers.models import (
    AnalysisReport,
    ThreatCategory,
    ThreatFinding,
    ThreatLevel,
)
from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.tool_safety import ToolSafetyVerifier

# Maps blocked patterns to threat metadata with real incident references
_THREAT_MAP: dict[str, tuple[ThreatCategory, ThreatLevel, str]] = {
    # Data destruction — CRITICAL
    "shutil": (
        ThreatCategory.DATA_DESTRUCTION,
        ThreatLevel.CRITICAL,
        "Replit Jul 2025: shutil.rmtree deleted 1,200 records during code freeze",
    ),
    "os.system": (
        ThreatCategory.DATA_DESTRUCTION,
        ThreatLevel.CRITICAL,
        "Claude Code 2025: os.system('terraform destroy') wiped 2.5yr database",
    ),
    "os.popen": (
        ThreatCategory.DATA_DESTRUCTION,
        ThreatLevel.CRITICAL,
        "Amazon Kiro Dec 2025: shell commands deleted production environment",
    ),
    # Code execution — HIGH
    "eval": (
        ThreatCategory.CODE_EXECUTION,
        ThreatLevel.HIGH,
        "Common vector: eval() enables arbitrary code execution from user input",
    ),
    "exec": (
        ThreatCategory.CODE_EXECUTION,
        ThreatLevel.HIGH,
        "Common vector: exec() runs arbitrary Python code",
    ),
    "compile": (
        ThreatCategory.CODE_EXECUTION,
        ThreatLevel.HIGH,
        "Common vector: compile() + exec() chain for obfuscated execution",
    ),
    "__import__": (
        ThreatCategory.CODE_EXECUTION,
        ThreatLevel.HIGH,
        "Common vector: dynamic import bypasses static analysis",
    ),
    "pickle": (
        ThreatCategory.CODE_EXECUTION,
        ThreatLevel.HIGH,
        "Deserialization attack: pickle.loads executes arbitrary code",
    ),
    # Secret exfiltration — MEDIUM
    "os.environ": (
        ThreatCategory.SECRET_EXFILTRATION,
        ThreatLevel.MEDIUM,
        "Common vector: reading production credentials for exfiltration",
    ),
    "os.getenv": (
        ThreatCategory.SECRET_EXFILTRATION,
        ThreatLevel.MEDIUM,
        "Common vector: accessing secret environment variables",
    ),
    # Network access — MEDIUM
    "socket": (
        ThreatCategory.NETWORK_ACCESS,
        ThreatLevel.MEDIUM,
        "Alibaba ROME 2025: agent created hidden reverse SSH tunnel",
    ),
    "http": (
        ThreatCategory.NETWORK_ACCESS,
        ThreatLevel.MEDIUM,
        "Common vector: outbound HTTP for data exfiltration",
    ),
    "requests": (
        ThreatCategory.NETWORK_ACCESS,
        ThreatLevel.MEDIUM,
        "Common vector: HTTP library for data exfiltration",
    ),
}


class ThreatClassifier:
    """Classifies code threats using Veridian's AST analysis + incident mapping.

    Architecture:
      1. Veridian's ToolSafetyVerifier does the AST analysis (the hard part)
      2. This classifier maps violations to threat categories + severities
      3. Each finding is linked to a real-world incident it prevents
    """

    def __init__(self) -> None:
        self._veridian = ToolSafetyVerifier()

    def analyze(self, code: str, code_id: str = "submission") -> AnalysisReport:
        """Full analysis: AST parsing + threat classification + incident mapping."""
        lines = code.strip().split("\n") if code.strip() else []
        report = AnalysisReport(code_id=code_id, total_lines=len(lines))

        # Step 1: Run Veridian's real ToolSafetyVerifier
        task = Task(id=code_id, title="Code safety check", verifier_id="tool_safety")
        result = TaskResult(raw_output=code, structured={"code": code})
        verdict = self._veridian.verify(task, result)

        report.veridian_verdict = "BLOCKED" if not verdict.passed else "PASSED"
        report.veridian_error = verdict.error or ""

        # Step 2: Classify each finding with incident references
        if not verdict.passed and verdict.error:
            report.threats = self._classify_from_error(verdict.error, code)

        # Step 3: Additional AST-level analysis for line numbers
        if code.strip():
            try:
                tree = ast.parse(code)
                report.threats.extend(self._ast_line_analysis(tree, code))
            except SyntaxError as e:
                report.threats.append(
                    ThreatFinding(
                        category=ThreatCategory.SYNTAX_ERROR,
                        level=ThreatLevel.HIGH,
                        description=f"Syntax error at line {e.lineno}: {e.msg}",
                        line_number=e.lineno or 0,
                        incident_ref="Unparseable code blocked — if we can't analyze it, we can't trust it",
                    )
                )

        # Deduplicate by description
        seen: set[str] = set()
        unique: list[ThreatFinding] = []
        for t in report.threats:
            if t.description not in seen:
                seen.add(t.description)
                unique.append(t)
        report.threats = unique

        return report

    def _classify_from_error(self, error: str, code: str) -> list[ThreatFinding]:
        """Map Veridian's error message to classified threat findings."""
        findings: list[ThreatFinding] = []
        error_lower = error.lower()

        for pattern, (category, level, incident) in _THREAT_MAP.items():
            if pattern.lower() in error_lower:
                findings.append(
                    ThreatFinding(
                        category=category,
                        level=level,
                        description=f"Blocked: {pattern}",
                        incident_ref=incident,
                    )
                )

        if not findings:
            # Generic finding from Veridian error
            findings.append(
                ThreatFinding(
                    category=ThreatCategory.CODE_EXECUTION,
                    level=ThreatLevel.HIGH,
                    description=error[:200],
                    incident_ref="Veridian ToolSafetyVerifier AST analysis",
                )
            )

        return findings

    def _ast_line_analysis(self, tree: ast.AST, code: str) -> list[ThreatFinding]:
        """Walk AST for line-level threat findings (enrichment only)."""
        findings: list[ThreatFinding] = []
        code_lines = code.split("\n")

        for node in ast.walk(tree):
            if not hasattr(node, "lineno"):
                continue
            line_no = node.lineno
            snippet = code_lines[line_no - 1].strip() if line_no <= len(code_lines) else ""

            # Check for shell command patterns in string literals
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                shell_threats = [
                    (
                        "rm -rf",
                        ThreatLevel.CRITICAL,
                        "Claude CLI 2025: rm -rf wiped home directory",
                    ),
                    ("DROP TABLE", ThreatLevel.CRITICAL, "Common: SQL DDL data destruction"),
                    ("DROP DATABASE", ThreatLevel.CRITICAL, "Common: database deletion"),
                    (
                        "terraform destroy",
                        ThreatLevel.CRITICAL,
                        "Claude Code 2025: terraform destroy wiped 2.5yr data",
                    ),
                    ("TRUNCATE TABLE", ThreatLevel.CRITICAL, "Common: table truncation"),
                    ("DELETE FROM", ThreatLevel.HIGH, "Common: mass data deletion"),
                ]
                for pattern, level, incident in shell_threats:
                    if pattern.lower() in val.lower():
                        findings.append(
                            ThreatFinding(
                                category=ThreatCategory.DATA_DESTRUCTION,
                                level=level,
                                description=f"Shell command contains '{pattern}'",
                                line_number=line_no,
                                code_snippet=snippet,
                                incident_ref=incident,
                            )
                        )

        return findings
