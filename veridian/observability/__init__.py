"""veridian.observability — Tracing, dashboard, audit, compliance, and CoT auditing."""

from veridian.observability.compliance_report import (
    ComplianceReport,
    ComplianceReportGenerator,
    ComplianceStandard,
)
from veridian.observability.cot_audit import (
    AlignmentViolation,
    CoTAuditor,
    CoTAuditResult,
    ViolationType,
)
from veridian.observability.dashboard import DASHBOARD_PORT, VeridianDashboard
from veridian.observability.otlp_exporter import (
    OTLPConfig,
    VerificationSpan,
    configure_otlp_tracer,
)
from veridian.observability.proof_chain import ProofChain, ProofEntry
from veridian.observability.tracer import TraceEvent, VeridianTracer

__all__ = [
    "AlignmentViolation",
    "CoTAuditResult",
    "CoTAuditor",
    "ComplianceReport",
    "ComplianceReportGenerator",
    "ComplianceStandard",
    "DASHBOARD_PORT",
    "OTLPConfig",
    "ProofChain",
    "ProofEntry",
    "TraceEvent",
    "VeridianDashboard",
    "VeridianTracer",
    "VerificationSpan",
    "ViolationType",
    "configure_otlp_tracer",
]
