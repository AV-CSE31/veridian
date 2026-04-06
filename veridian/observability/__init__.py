"""veridian.observability — Tracing, dashboard, audit, compliance, and ops ingest."""

from veridian.observability.alerts import (
    Alert,
    AlertManager,
    AlertRule,
    AlertSeverity,
    AlertSink,
    LogAlertSink,
)
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
from veridian.observability.ingest import (
    BackpressurePolicy,
    IngestBuffer,
    IngestPipeline,
    IngestSink,
    JSONLSink,
)
from veridian.observability.otlp_exporter import (
    OTLPConfig,
    VerificationSpan,
    configure_otlp_tracer,
)
from veridian.observability.proof_chain import ProofChain, ProofEntry
from veridian.observability.retention import RetentionManager, RetentionPolicy
from veridian.observability.slo import (
    BUILTIN_SLOS,
    SLOComparison,
    SLODefinition,
    SLOEvaluator,
    SLOReport,
)
from veridian.observability.tracer import TraceEvent, VeridianTracer

__all__ = [
    "BUILTIN_SLOS",
    "Alert",
    "AlertManager",
    "AlertRule",
    "AlertSeverity",
    "AlertSink",
    "AlignmentViolation",
    "BackpressurePolicy",
    "CoTAuditResult",
    "CoTAuditor",
    "ComplianceReport",
    "ComplianceReportGenerator",
    "ComplianceStandard",
    "DASHBOARD_PORT",
    "IngestBuffer",
    "IngestPipeline",
    "IngestSink",
    "JSONLSink",
    "LogAlertSink",
    "OTLPConfig",
    "ProofChain",
    "ProofEntry",
    "RetentionManager",
    "RetentionPolicy",
    "SLOComparison",
    "SLODefinition",
    "SLOEvaluator",
    "SLOReport",
    "TraceEvent",
    "VeridianDashboard",
    "VeridianTracer",
    "VerificationSpan",
    "ViolationType",
    "configure_otlp_tracer",
]
