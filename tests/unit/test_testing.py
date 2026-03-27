"""
tests/unit/test_testing.py
───────────────────────────
Tests for A4: veridian testing framework with record/replay.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veridian.core.task import Task, TaskResult
from veridian.testing.recorder import AgentRecorder, RecordedRun
from veridian.testing.replayer import ReplayAssertion, Replayer

# ── RecordedRun ──────────────────────────────────────────────────────────────


class TestRecordedRun:
    def test_fields(self) -> None:
        task = Task(title="test task", description="do something", verifier_id="schema")
        result = TaskResult(raw_output="done", structured={"key": "value"})
        rec = RecordedRun(
            run_id="run-001",
            task=task,
            result=result,
            verification_passed=True,
            verification_error=None,
        )
        assert rec.run_id == "run-001"
        assert rec.verification_passed is True

    def test_to_dict_is_json_serializable(self) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="ok")
        rec = RecordedRun(
            run_id="r1",
            task=task,
            result=result,
            verification_passed=True,
        )
        d = rec.to_dict()
        # must be JSON-serializable
        json.dumps(d)

    def test_to_dict_has_required_keys(self) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="ok")
        rec = RecordedRun(run_id="r1", task=task, result=result, verification_passed=True)
        d = rec.to_dict()
        assert "run_id" in d
        assert "task" in d
        assert "result" in d
        assert "verification_passed" in d

    def test_from_dict_roundtrip(self) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="ok", structured={"x": 1})
        rec = RecordedRun(run_id="r1", task=task, result=result, verification_passed=True)
        recovered = RecordedRun.from_dict(rec.to_dict())
        assert recovered.run_id == "r1"
        assert recovered.verification_passed is True
        assert recovered.result.structured["x"] == 1


# ── AgentRecorder ─────────────────────────────────────────────────────────────


class TestAgentRecorder:
    @pytest.fixture
    def recorder(self, tmp_path: Path) -> AgentRecorder:
        return AgentRecorder(trace_dir=tmp_path / "traces")

    def test_record_creates_file(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done")
        recorder.record(
            run_id="run-001",
            task=task,
            result=result,
            verification_passed=True,
        )
        trace_files = list((tmp_path / "traces").glob("*.jsonl"))
        assert len(trace_files) == 1

    def test_record_multiple_runs(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done")
        for i in range(3):
            recorder.record(
                run_id=f"run-{i:03d}",
                task=task,
                result=result,
                verification_passed=True,
            )
        # All runs in one file or multiple files, but at least one
        files = list((tmp_path / "traces").rglob("*.jsonl"))
        assert len(files) >= 1

    def test_record_file_is_valid_jsonl(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done")
        recorder.record(run_id="run-001", task=task, result=result, verification_passed=True)
        trace_file = next((tmp_path / "traces").glob("*.jsonl"))
        for line in trace_file.read_text().strip().splitlines():
            json.loads(line)  # must parse without error

    def test_load_returns_recorded_runs(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done", structured={"summary": "all good"})
        recorder.record(run_id="run-001", task=task, result=result, verification_passed=True)
        runs = recorder.load()
        assert len(runs) >= 1
        assert runs[0].run_id == "run-001"


# ── Replayer + ReplayAssertion ─────────────────────────────────────────────────


class TestReplayAssertion:
    def test_assertion_passed_true(self) -> None:
        assertion = ReplayAssertion(
            name="should_pass",
            check=lambda rec: rec.verification_passed,
        )
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done")
        rec = RecordedRun(run_id="r1", task=task, result=result, verification_passed=True)
        assert assertion.evaluate(rec) is True

    def test_assertion_passed_false(self) -> None:
        assertion = ReplayAssertion(
            name="should_fail",
            check=lambda rec: rec.verification_passed,
        )
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="")
        rec = RecordedRun(run_id="r1", task=task, result=result, verification_passed=False)
        assert assertion.evaluate(rec) is False

    def test_assertion_on_structured_output(self) -> None:
        assertion = ReplayAssertion(
            name="has_summary",
            check=lambda rec: "summary" in rec.result.structured,
        )
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="ok", structured={"summary": "done"})
        rec = RecordedRun(run_id="r1", task=task, result=result, verification_passed=True)
        assert assertion.evaluate(rec) is True


class TestReplayer:
    @pytest.fixture
    def recorder(self, tmp_path: Path) -> AgentRecorder:
        return AgentRecorder(trace_dir=tmp_path / "traces")

    def test_replay_passes_all_assertions(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="done", structured={"status": "ok"})
        recorder.record(run_id="run-001", task=task, result=result, verification_passed=True)

        replayer = Replayer(recorder=recorder)
        replayer.add_assertion(ReplayAssertion("passes", check=lambda r: r.verification_passed))
        replayer.add_assertion(
            ReplayAssertion("has_status", check=lambda r: r.result.structured.get("status") == "ok")
        )
        results = replayer.run()
        assert all(r.passed for r in results)

    def test_replay_fails_failed_assertion(self, recorder: AgentRecorder, tmp_path: Path) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="", structured={})
        recorder.record(run_id="run-001", task=task, result=result, verification_passed=False)

        replayer = Replayer(recorder=recorder)
        replayer.add_assertion(ReplayAssertion("must_pass", check=lambda r: r.verification_passed))
        results = replayer.run()
        assert any(not r.passed for r in results)

    def test_replay_result_has_assertion_name(
        self, recorder: AgentRecorder, tmp_path: Path
    ) -> None:
        task = Task(title="t", description="d", verifier_id="schema")
        result = TaskResult(raw_output="ok")
        recorder.record(run_id="r1", task=task, result=result, verification_passed=True)
        replayer = Replayer(recorder=recorder)
        replayer.add_assertion(ReplayAssertion("my_check", check=lambda r: True))
        results = replayer.run()
        assert results[0].assertion_name == "my_check"
