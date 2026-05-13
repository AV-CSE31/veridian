"""
tests.unit.test_phase4b_stretch
───────────────────────────────
Acceptance tests for the Phase 4.B follow-up batch:

* ``AgentRecorder._atomic_append`` no longer rewrites the whole trace
  file per record. The new path appends under a FileLock; concurrent
  recorders serialise on the lock rather than racing on temp-file
  renames, and load() still tolerates a half-written trailing line.
* ``checkpoint_cursor._trace_step_index`` caches the
  ``step_id → index`` map per (list-id, list-len) pair, turning the
  previous O(n) scan into O(1) on the hot replay path.
"""

from __future__ import annotations

import threading
from pathlib import Path

from veridian.core.task import Task, TaskResult, TraceStep
from veridian.loop.checkpoint_cursor import (
    _TRACE_STEP_INDEX_CACHE,
    _trace_step_index,
)
from veridian.testing.recorder import AgentRecorder

# ── Recorder atomic-append ──────────────────────────────────────────────────


def _mk_task() -> Task:
    return Task(id="t-1", title="t", verifier_id="schema")


def _mk_result() -> TaskResult:
    return TaskResult(raw_output="ok")


class TestRecorderAppend:
    def test_repeated_records_grow_file_monotonically(self, tmp_path: Path) -> None:
        rec = AgentRecorder(trace_dir=tmp_path)
        for i in range(20):
            rec.record(
                run_id=f"run-{i}", task=_mk_task(), result=_mk_result(), verification_passed=True
            )
        runs = rec.load()
        assert len(runs) == 20

    def test_concurrent_appends_do_not_lose_records(self, tmp_path: Path) -> None:
        rec = AgentRecorder(trace_dir=tmp_path)

        def worker(i: int) -> None:
            rec.record(
                run_id=f"run-{i}", task=_mk_task(), result=_mk_result(), verification_passed=True
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        runs = rec.load()
        assert len(runs) == 16

    def test_load_skips_partial_trailing_line(self, tmp_path: Path) -> None:
        rec = AgentRecorder(trace_dir=tmp_path)
        rec.record(run_id="good", task=_mk_task(), result=_mk_result(), verification_passed=True)

        # Simulate a crash mid-write: append an unterminated, malformed
        # line. The tolerant loader must drop it without raising.
        trace_file = next(tmp_path.glob("*.jsonl"))
        with trace_file.open("ab") as fh:
            fh.write(b'{"run_id": "partial')

        runs = rec.load()
        assert len(runs) == 1
        assert runs[0].run_id == "good"


# ── TraceStep lookup cache ──────────────────────────────────────────────────


def _step(step_id: str) -> TraceStep:
    return TraceStep(
        step_id=step_id,
        role="assistant",
        action_type="reason",
        content="x",
        timestamp_ms=0,
    )


class TestTraceStepLookupCache:
    def setup_method(self) -> None:
        _TRACE_STEP_INDEX_CACHE.clear()

    def test_returns_correct_index(self) -> None:
        result = TaskResult(raw_output="")
        result.trace_steps.extend([_step(f"s{i}") for i in range(50)])
        assert _trace_step_index(result, "s17") == 17
        assert _trace_step_index(result, "missing") is None

    def test_cache_is_reused_on_repeat_lookup(self) -> None:
        result = TaskResult(raw_output="")
        result.trace_steps.extend([_step(f"s{i}") for i in range(10)])
        _trace_step_index(result, "s3")
        before = dict(_TRACE_STEP_INDEX_CACHE)
        _trace_step_index(result, "s7")  # same list → same cache entry
        assert before == _TRACE_STEP_INDEX_CACHE

    def test_cache_invalidates_on_append(self) -> None:
        result = TaskResult(raw_output="")
        result.trace_steps.extend([_step(f"s{i}") for i in range(5)])
        _trace_step_index(result, "s3")
        cache_after_first = dict(_TRACE_STEP_INDEX_CACHE)

        # Append a new step → list length changes → cache key changes.
        result.trace_steps.append(_step("s_new"))
        _trace_step_index(result, "s_new")
        # New cache entry must exist; old entry may still exist briefly
        # until the trim threshold but the keyspace has at least grown.
        assert len(_TRACE_STEP_INDEX_CACHE) > len(cache_after_first) or any(
            "s_new" in mapping for mapping in _TRACE_STEP_INDEX_CACHE.values()
        )

    def test_cache_trims_after_threshold(self) -> None:
        # Force the trim path by creating 300 distinct (id, len) keys.
        for i in range(300):
            r = TaskResult(raw_output="")
            r.trace_steps.append(_step(f"unique-{i}"))
            _trace_step_index(r, f"unique-{i}")
        # After the trim the cache must stay bounded.
        assert len(_TRACE_STEP_INDEX_CACHE) <= 257  # 256 cap + at most one growth pass
