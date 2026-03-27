"""
tests/unit/test_otlp_exporter.py
─────────────────────────────────
Tests for A2: OTel OTLP verification trace exporter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veridian.observability.otlp_exporter import (
    OTLPConfig,
    VerificationSpan,
    configure_otlp_tracer,
)
from veridian.observability.tracer import VeridianTracer


class TestOTLPConfig:
    def test_default_endpoint(self) -> None:
        cfg = OTLPConfig()
        assert cfg.endpoint == "http://localhost:4318/v1/traces"

    def test_custom_endpoint(self) -> None:
        cfg = OTLPConfig(endpoint="http://otel-collector:4318/v1/traces")
        assert "otel-collector" in cfg.endpoint

    def test_service_name_default(self) -> None:
        assert OTLPConfig().service_name == "veridian"

    def test_custom_service_name(self) -> None:
        assert OTLPConfig(service_name="my-agent").service_name == "my-agent"

    def test_headers_default_empty(self) -> None:
        assert OTLPConfig().headers == {}

    def test_custom_headers(self) -> None:
        cfg = OTLPConfig(headers={"Authorization": "Bearer token"})
        assert cfg.headers["Authorization"] == "Bearer token"


class TestConfigureOTLPTracer:
    def test_returns_veridian_tracer(self, tmp_path: Path) -> None:
        tracer = configure_otlp_tracer(
            config=OTLPConfig(),
            trace_file=tmp_path / "trace.jsonl",
            use_otel=False,
        )
        assert isinstance(tracer, VeridianTracer)

    def test_returns_tracer_without_sdk(self, tmp_path: Path) -> None:
        tracer = configure_otlp_tracer(
            config=OTLPConfig(),
            trace_file=tmp_path / "trace.jsonl",
            use_otel=False,
        )
        assert isinstance(tracer, VeridianTracer)


class TestVerificationSpan:
    def test_dataclass_fields(self) -> None:
        span = VerificationSpan(
            task_id="t-001",
            verifier_id="schema",
            passed=True,
            confidence=0.95,
            provenance_hash="abc123",
        )
        assert span.task_id == "t-001"
        assert span.verifier_id == "schema"
        assert span.passed is True
        assert span.confidence == 0.95
        assert span.provenance_hash == "abc123"

    def test_optional_fields_default(self) -> None:
        span = VerificationSpan(task_id="t-001", verifier_id="schema", passed=False)
        assert span.confidence is None
        assert span.provenance_hash is None
        assert span.error is None

    def test_failed_span_has_error(self) -> None:
        span = VerificationSpan(
            task_id="t-001", verifier_id="schema", passed=False, error="field missing"
        )
        assert span.error == "field missing"

    def test_to_dict_includes_required_fields(self) -> None:
        span = VerificationSpan(
            task_id="t-001",
            verifier_id="bash_exit",
            passed=True,
            confidence=0.8,
            provenance_hash="deadbeef",
        )
        d = span.to_dict()
        assert d["veridian.task.id"] == "t-001"
        assert d["veridian.verification.verifier_id"] == "bash_exit"
        assert d["veridian.verification.passed"] is True
        assert d["veridian.verification.confidence"] == 0.8
        assert d["veridian.verification.provenance_hash"] == "deadbeef"

    def test_to_dict_omits_none_fields(self) -> None:
        d = VerificationSpan(task_id="t-001", verifier_id="schema", passed=True).to_dict()
        assert "veridian.verification.confidence" not in d
        assert "veridian.verification.provenance_hash" not in d
        assert "veridian.verification.error" not in d


class TestTraceVerificationInTracer:
    @pytest.fixture
    def trace_file(self, tmp_path: Path) -> Path:
        return tmp_path / "trace.jsonl"

    @pytest.fixture
    def tracer(self, trace_file: Path) -> VeridianTracer:
        return VeridianTracer(trace_file=trace_file, use_otel=False)

    def test_trace_verification_writes_jsonl(
        self, tracer: VeridianTracer, trace_file: Path
    ) -> None:
        tracer.start_trace(run_id="run-001")
        tracer.trace_verification(
            VerificationSpan(task_id="t-001", verifier_id="schema", passed=True, confidence=0.9)
        )
        tracer.end_trace()
        events = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        vev = next(e for e in events if e["event_type"] == "verification_step")
        assert vev["attributes"]["veridian.verification.verifier_id"] == "schema"
        assert vev["attributes"]["veridian.verification.passed"] is True

    def test_trace_verification_passed_false(
        self, tracer: VeridianTracer, trace_file: Path
    ) -> None:
        tracer.start_trace(run_id="run-002")
        tracer.trace_verification(
            VerificationSpan(task_id="t-002", verifier_id="bash_exit", passed=False, error="exit 1")
        )
        tracer.end_trace()
        events = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        vev = next(e for e in events if e["event_type"] == "verification_step")
        assert vev["attributes"]["veridian.verification.passed"] is False
        assert vev["attributes"]["veridian.verification.error"] == "exit 1"

    def test_trace_verification_confidence_and_provenance(
        self, tracer: VeridianTracer, trace_file: Path
    ) -> None:
        tracer.start_trace(run_id="run-003")
        tracer.trace_verification(
            VerificationSpan(
                task_id="t-003",
                verifier_id="schema",
                passed=True,
                confidence=0.72,
                provenance_hash="sha256:cafebabe",
            )
        )
        tracer.end_trace()
        events = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        vev = next(e for e in events if e["event_type"] == "verification_step")
        assert vev["attributes"]["veridian.verification.confidence"] == 0.72
        assert vev["attributes"]["veridian.verification.provenance_hash"] == "sha256:cafebabe"

    def test_multiple_verifications_all_written(
        self, tracer: VeridianTracer, trace_file: Path
    ) -> None:
        tracer.start_trace(run_id="run-004")
        for i, vid in enumerate(["schema", "bash_exit", "llm_judge"]):
            tracer.trace_verification(
                VerificationSpan(task_id=f"t-{i:03d}", verifier_id=vid, passed=i % 2 == 0)
            )
        tracer.end_trace()
        events = [json.loads(line) for line in trace_file.read_text().strip().splitlines()]
        vevs = [e for e in events if e["event_type"] == "verification_step"]
        assert len(vevs) == 3
        ids = [v["attributes"]["veridian.verification.verifier_id"] for v in vevs]
        assert set(ids) == {"schema", "bash_exit", "llm_judge"}
