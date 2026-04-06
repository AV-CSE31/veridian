"""
Tests for veridian.observability.ingest — Scalable Observability Ingest Pipeline.
TDD: RED phase (WCP-022).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from veridian.observability.ingest import (
    BackpressurePolicy,
    IngestBuffer,
    IngestPipeline,
    IngestSink,
    JSONLSink,
)

# ── IngestSink ABC ─────────────────────────────────────────────────────────────


class TestIngestSinkABC:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            IngestSink()  # type: ignore[abstract]

    def test_concrete_subclass_is_instantiable(self) -> None:
        class DummySink(IngestSink):
            def write(self, events: list[dict[str, Any]]) -> None:
                pass

            def flush(self) -> None:
                pass

        sink = DummySink()
        assert sink is not None


# ── IngestBuffer ───────────────────────────────────────────────────────────────


class TestIngestBufferAccumulation:
    def test_accumulates_events(self) -> None:
        buf = IngestBuffer(batch_size=10)
        sink = MagicMock(spec=IngestSink)
        buf.emit({"type": "a"}, sink=sink)
        buf.emit({"type": "b"}, sink=sink)
        # Should not have flushed yet (batch_size=10)
        sink.write.assert_not_called()

    def test_flushes_when_batch_size_reached(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(batch_size=3, flush_interval_seconds=9999.0)
        buf.emit({"n": 1}, sink=sink)
        buf.emit({"n": 2}, sink=sink)
        sink.write.assert_not_called()
        buf.emit({"n": 3}, sink=sink)
        sink.write.assert_called_once()
        events = sink.write.call_args[0][0]
        assert len(events) == 3

    def test_flushes_when_time_interval_expires(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(batch_size=999, flush_interval_seconds=0.5)
        buf.emit({"n": 1}, sink=sink)
        sink.write.assert_not_called()
        # Simulate time passing by patching time.monotonic
        with patch(
            "veridian.observability.ingest.time.monotonic", return_value=time.monotonic() + 1.0
        ):
            buf.emit({"n": 2}, sink=sink)
        sink.write.assert_called_once()

    def test_manual_flush_writes_buffered_events(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(batch_size=999)
        buf.emit({"n": 1}, sink=sink)
        buf.emit({"n": 2}, sink=sink)
        sink.write.assert_not_called()
        buf.flush(sink=sink)
        sink.write.assert_called_once()
        events = sink.write.call_args[0][0]
        assert len(events) == 2

    def test_manual_flush_noop_when_empty(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(batch_size=999)
        buf.flush(sink=sink)
        sink.write.assert_not_called()


# ── Backpressure ───────────────────────────────────────────────────────────────


class TestBackpressure:
    def test_drop_oldest_when_buffer_full(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(
            batch_size=999,
            flush_interval_seconds=9999.0,
            max_buffer_size=3,
            backpressure=BackpressurePolicy.DROP_OLDEST,
        )
        buf.emit({"n": 1}, sink=sink)
        buf.emit({"n": 2}, sink=sink)
        buf.emit({"n": 3}, sink=sink)
        # Buffer is full; adding one more should drop oldest
        buf.emit({"n": 4}, sink=sink)
        buf.flush(sink=sink)
        events = sink.write.call_args[0][0]
        assert len(events) == 3
        assert events[0]["n"] == 2
        assert events[2]["n"] == 4

    def test_block_when_buffer_full(self) -> None:
        sink = MagicMock(spec=IngestSink)
        buf = IngestBuffer(
            batch_size=999,
            flush_interval_seconds=9999.0,
            max_buffer_size=3,
            backpressure=BackpressurePolicy.BLOCK,
        )
        buf.emit({"n": 1}, sink=sink)
        buf.emit({"n": 2}, sink=sink)
        buf.emit({"n": 3}, sink=sink)

        # Emit in a thread; it should block until space is freed
        added = threading.Event()

        def _emit_blocked() -> None:
            buf.emit({"n": 4}, sink=sink)
            added.set()

        t = threading.Thread(target=_emit_blocked)
        t.start()
        # Give thread a moment to start blocking
        time.sleep(0.05)
        assert not added.is_set(), "emit() should block when buffer full"
        # Free space by flushing
        buf.flush(sink=sink)
        t.join(timeout=2.0)
        assert added.is_set(), "emit() should unblock after flush"


# ── JSONLSink ──────────────────────────────────────────────────────────────────


class TestJSONLSink:
    def test_writes_events_atomically(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        sink.write([{"event": "a"}, {"event": "b"}])
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "a"
        assert json.loads(lines[1])["event"] == "b"

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        sink.write([{"event": "a"}])
        sink.write([{"event": "b"}])
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "dir" / "events.jsonl"
        sink = JSONLSink(path=out)
        sink.write([{"event": "a"}])
        assert out.exists()

    def test_flush_is_noop(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        sink.flush()  # Should not raise


# ── IngestPipeline ─────────────────────────────────────────────────────────────


class TestIngestPipeline:
    def test_routes_events_through_filter_buffer_sink(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        buf = IngestBuffer(batch_size=2, flush_interval_seconds=9999.0)
        pipeline = IngestPipeline(sink=sink, buffer=buf)
        pipeline.emit({"type": "a"})
        pipeline.emit({"type": "b"})
        # batch_size=2 so should have flushed
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_filter_drops_events(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        buf = IngestBuffer(batch_size=1, flush_interval_seconds=9999.0)

        def drop_debug(event: dict[str, Any]) -> bool:
            return event.get("level") != "debug"

        pipeline = IngestPipeline(sink=sink, buffer=buf, filters=[drop_debug])
        pipeline.emit({"level": "debug", "msg": "skip"})
        pipeline.emit({"level": "info", "msg": "keep"})
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["level"] == "info"

    def test_shutdown_flushes_remaining(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        buf = IngestBuffer(batch_size=999, flush_interval_seconds=9999.0)
        pipeline = IngestPipeline(sink=sink, buffer=buf)
        pipeline.emit({"type": "a"})
        # Not yet flushed
        assert not out.exists() or out.read_text().strip() == ""
        pipeline.shutdown()
        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1


# ── Thread Safety ──────────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_emit_does_not_corrupt_state(self, tmp_path: Path) -> None:
        out = tmp_path / "events.jsonl"
        sink = JSONLSink(path=out)
        buf = IngestBuffer(
            batch_size=50,
            flush_interval_seconds=9999.0,
            max_buffer_size=10000,
        )
        pipeline = IngestPipeline(sink=sink, buffer=buf)

        n_threads = 10
        n_events_per_thread = 100
        barrier = threading.Barrier(n_threads)

        def _emit_many(thread_id: int) -> None:
            barrier.wait()
            for i in range(n_events_per_thread):
                pipeline.emit({"thread": thread_id, "seq": i})

        threads = [threading.Thread(target=_emit_many, args=(tid,)) for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        pipeline.shutdown()

        total_expected = n_threads * n_events_per_thread
        lines = out.read_text().strip().splitlines()
        assert len(lines) == total_expected
        # Verify every line is valid JSON
        for line in lines:
            json.loads(line)
