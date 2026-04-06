"""Typed models for the code safety analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ThreatLevel(Enum):
    """Severity classification for detected threats."""

    CRITICAL = "critical"  # Immediate data destruction risk (rm -rf, DROP TABLE)
    HIGH = "high"  # Arbitrary code execution (eval, exec, pickle)
    MEDIUM = "medium"  # Information leakage (os.environ, socket)
    LOW = "low"  # Potentially unsafe but context-dependent
    SAFE = "safe"  # No threats detected


class ThreatCategory(Enum):
    """Classification of the threat type."""

    DATA_DESTRUCTION = "data_destruction"  # rm -rf, DROP TABLE, shutil.rmtree
    CODE_EXECUTION = "code_execution"  # eval, exec, pickle, __import__
    SECRET_EXFILTRATION = "secret_exfiltration"  # os.environ, credential access
    NETWORK_ACCESS = "network_access"  # socket, http, outbound connections
    PRIVILEGE_ESCALATION = "privilege_escalation"  # sudo, chmod, setuid
    SYNTAX_ERROR = "syntax_error"  # Unparseable code


@dataclass
class ThreatFinding:
    """A single threat detected in agent-generated code."""

    category: ThreatCategory
    level: ThreatLevel
    description: str
    line_number: int = 0
    code_snippet: str = ""
    incident_ref: str = ""  # Reference to the real-world incident this prevents

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "level": self.level.value,
            "description": self.description,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
            "incident_ref": self.incident_ref,
        }


@dataclass
class AnalysisReport:
    """Complete analysis report for a code submission."""

    code_id: str = ""
    total_lines: int = 0
    threats: list[ThreatFinding] = field(default_factory=list)
    veridian_verdict: str = ""  # "BLOCKED" | "PASSED"
    veridian_error: str = ""

    @property
    def max_threat_level(self) -> ThreatLevel:
        if not self.threats:
            return ThreatLevel.SAFE
        priority = {
            ThreatLevel.CRITICAL: 0,
            ThreatLevel.HIGH: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.LOW: 3,
            ThreatLevel.SAFE: 4,
        }
        return min(self.threats, key=lambda t: priority[t.level]).level

    @property
    def blocked(self) -> bool:
        return self.max_threat_level in (ThreatLevel.CRITICAL, ThreatLevel.HIGH, ThreatLevel.MEDIUM)

    def to_dict(self) -> dict[str, object]:
        return {
            "code_id": self.code_id,
            "total_lines": self.total_lines,
            "threats_found": len(self.threats),
            "max_threat_level": self.max_threat_level.value,
            "blocked": self.blocked,
            "veridian_verdict": self.veridian_verdict,
            "threats": [t.to_dict() for t in self.threats],
        }
