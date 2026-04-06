"""
tests.unit.test_activity_boundary
───────────────────────────────────
WCP-010: Activity Boundary Expansion — enforce run_activity() for ALL
external side effects.

Tests the typed activity wrappers that ensure HTTP calls, file I/O, and
subprocess invocations are routed through the ActivityJournal for
deterministic replay, deduplication, and audit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from veridian.loop.activity import ActivityError, ActivityJournal, RetryPolicy
from veridian.loop.activity_boundary import (
    ActivityBoundaryError,
    file_activity,
    http_activity,
    subprocess_activity,
)


class TestHttpActivity:
    """http_activity wraps HTTP calls and records them in the journal."""

    def test_http_activity_records_success(self) -> None:
        journal = ActivityJournal()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_response.headers = {"content-type": "text/plain"}

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response
            result = http_activity(
                journal=journal,
                activity_id="http_check_1",
                method="GET",
                url="https://example.com/health",
            )

        assert result["status_code"] == 200
        assert result["text"] == "OK"
        record = journal.get("http_check_1")
        assert record is not None
        assert record.status == "success"
        assert record.fn_name == "http_request"

    def test_http_activity_records_post_with_kwargs(self) -> None:
        journal = ActivityJournal()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = '{"id": 1}'
        mock_response.headers = {"content-type": "application/json"}

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response
            result = http_activity(
                journal=journal,
                activity_id="http_post_1",
                method="POST",
                url="https://example.com/items",
                timeout=30,
            )

        assert result["status_code"] == 201
        mock_httpx.request.assert_called_once_with("POST", "https://example.com/items", timeout=30)

    def test_http_activity_failure_raises_activity_error(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.side_effect = ConnectionError("unreachable")
            with pytest.raises(ActivityError):
                http_activity(
                    journal=journal,
                    activity_id="http_fail_1",
                    method="GET",
                    url="https://unreachable.test",
                    retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
                )

        record = journal.get("http_fail_1")
        assert record is not None
        assert record.status == "failed"

    def test_http_activity_journal_entry_has_correct_fields(self) -> None:
        journal = ActivityJournal()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "pong"
        mock_response.headers = {"x-custom": "val"}

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response
            http_activity(
                journal=journal,
                activity_id="http_fields_1",
                method="HEAD",
                url="https://example.com/ping",
            )

        record = journal.get("http_fields_1")
        assert record is not None
        assert record.idempotency_key == "http_fields_1"
        assert record.fn_name == "http_request"
        assert record.attempts >= 1
        assert record.timestamp_ms > 0


class TestFileActivity:
    """file_activity wraps file I/O checks and records them in the journal."""

    def test_file_activity_check_exists_records_result(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.stat.return_value = MagicMock(st_size=1024)
            MockPath.return_value = mock_path

            result = file_activity(
                journal=journal,
                activity_id="file_check_1",
                path="/tmp/report.json",
                operation="exists",
            )

        assert result["exists"] is True
        assert result["size"] == 1024
        record = journal.get("file_check_1")
        assert record is not None
        assert record.status == "success"
        assert record.fn_name == "file_check"

    def test_file_activity_check_not_exists(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path

            result = file_activity(
                journal=journal,
                activity_id="file_check_2",
                path="/tmp/missing.txt",
                operation="exists",
            )

        assert result["exists"] is False
        assert result["size"] is None

    def test_file_activity_read_records_content(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.stat.return_value = MagicMock(st_size=5)
            mock_path.read_text.return_value = "hello"
            MockPath.return_value = mock_path

            result = file_activity(
                journal=journal,
                activity_id="file_read_1",
                path="/tmp/data.txt",
                operation="read",
            )

        assert result["content"] == "hello"
        assert result["exists"] is True
        record = journal.get("file_read_1")
        assert record is not None
        assert record.status == "success"

    def test_file_activity_stat_records_metadata(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            stat_result = MagicMock(st_size=2048, st_mtime=1700000000.0)
            mock_path.stat.return_value = stat_result
            MockPath.return_value = mock_path

            result = file_activity(
                journal=journal,
                activity_id="file_stat_1",
                path="/tmp/data.bin",
                operation="stat",
            )

        assert result["exists"] is True
        assert result["size"] == 2048
        assert result["mtime"] == 1700000000.0

    def test_file_activity_invalid_operation_raises(self) -> None:
        journal = ActivityJournal()
        with pytest.raises(ActivityBoundaryError, match="Unsupported file operation"):
            file_activity(
                journal=journal,
                activity_id="file_bad_1",
                path="/tmp/x",
                operation="delete",
            )


class TestSubprocessActivity:
    """subprocess_activity wraps subprocess calls and records them."""

    def test_subprocess_activity_records_success(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "output line\n"
            mock_proc.stderr = ""
            mock_sub.run.return_value = mock_proc

            result = subprocess_activity(
                journal=journal,
                activity_id="cmd_1",
                cmd=["echo", "hello"],
            )

        assert result["exit_code"] == 0
        assert result["stdout"] == "output line\n"
        record = journal.get("cmd_1")
        assert record is not None
        assert record.status == "success"
        assert record.fn_name == "subprocess_exec"

    def test_subprocess_activity_records_nonzero_exit(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stdout = ""
            mock_proc.stderr = "error occurred"
            mock_sub.run.return_value = mock_proc

            result = subprocess_activity(
                journal=journal,
                activity_id="cmd_2",
                cmd=["false"],
            )

        # Non-zero exit is NOT an exception — it is a valid result
        assert result["exit_code"] == 1
        assert result["stderr"] == "error occurred"
        record = journal.get("cmd_2")
        assert record is not None
        assert record.status == "success"

    def test_subprocess_activity_timeout_raises(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            import subprocess as real_sub

            mock_sub.run.side_effect = real_sub.TimeoutExpired(cmd="sleep", timeout=1)
            mock_sub.TimeoutExpired = real_sub.TimeoutExpired

            with pytest.raises(ActivityError):
                subprocess_activity(
                    journal=journal,
                    activity_id="cmd_timeout_1",
                    cmd=["sleep", "100"],
                    timeout_seconds=1,
                    retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
                )

        record = journal.get("cmd_timeout_1")
        assert record is not None
        assert record.status == "failed"

    def test_subprocess_activity_passes_kwargs(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_sub.run.return_value = mock_proc

            subprocess_activity(
                journal=journal,
                activity_id="cmd_kwargs_1",
                cmd=["ls", "-la"],
                timeout_seconds=60,
                cwd="/tmp",
            )

        mock_sub.run.assert_called_once_with(
            ["ls", "-la"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd="/tmp",
        )


class TestDuplicateDetection:
    """Same activity_id on replay is skipped and returns cached result."""

    def test_http_duplicate_returns_cached(self) -> None:
        journal = ActivityJournal()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "cached"
        mock_response.headers = {}

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response

            result1 = http_activity(
                journal=journal,
                activity_id="dup_http_1",
                method="GET",
                url="https://example.com",
            )

        # Second call — httpx should NOT be called again
        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.side_effect = AssertionError("should not be called")

            result2 = http_activity(
                journal=journal,
                activity_id="dup_http_1",
                method="GET",
                url="https://example.com",
            )

        assert result1 == result2
        assert result1["status_code"] == 200

    def test_file_duplicate_returns_cached(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.stat.return_value = MagicMock(st_size=100)
            MockPath.return_value = mock_path

            result1 = file_activity(
                journal=journal,
                activity_id="dup_file_1",
                path="/tmp/test.txt",
                operation="exists",
            )

        # Second call — Path should NOT be called again
        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            MockPath.side_effect = AssertionError("should not be called")

            result2 = file_activity(
                journal=journal,
                activity_id="dup_file_1",
                path="/tmp/test.txt",
                operation="exists",
            )

        assert result1 == result2
        assert result1["exists"] is True

    def test_subprocess_duplicate_returns_cached(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "cached_output"
            mock_proc.stderr = ""
            mock_sub.run.return_value = mock_proc

            result1 = subprocess_activity(
                journal=journal,
                activity_id="dup_cmd_1",
                cmd=["echo", "test"],
            )

        # Second call — subprocess should NOT be called again
        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_sub.run.side_effect = AssertionError("should not be called")

            result2 = subprocess_activity(
                journal=journal,
                activity_id="dup_cmd_1",
                cmd=["echo", "test"],
            )

        assert result1 == result2
        assert result1["stdout"] == "cached_output"


class TestJournalEntries:
    """All boundary types produce proper activity journal entries."""

    def test_http_journal_entry_complete(self) -> None:
        journal = ActivityJournal()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.headers = {}

        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response
            http_activity(
                journal=journal,
                activity_id="je_http_1",
                method="GET",
                url="https://example.com",
            )

        entries = journal.to_list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["activity_id"].startswith("act_")
        assert entry["idempotency_key"] == "je_http_1"
        assert entry["fn_name"] == "http_request"
        assert entry["status"] == "success"
        assert entry["attempts"] >= 1
        assert entry["timestamp_ms"] > 0
        assert isinstance(entry["result"], dict)
        assert "status_code" in entry["result"]

    def test_file_journal_entry_complete(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.stat.return_value = MagicMock(st_size=42)
            MockPath.return_value = mock_path

            file_activity(
                journal=journal,
                activity_id="je_file_1",
                path="/tmp/x.txt",
                operation="exists",
            )

        entries = journal.to_list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["idempotency_key"] == "je_file_1"
        assert entry["fn_name"] == "file_check"
        assert entry["status"] == "success"
        assert isinstance(entry["result"], dict)
        assert "exists" in entry["result"]

    def test_subprocess_journal_entry_complete(self) -> None:
        journal = ActivityJournal()

        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "done"
            mock_proc.stderr = ""
            mock_sub.run.return_value = mock_proc

            subprocess_activity(
                journal=journal,
                activity_id="je_cmd_1",
                cmd=["echo", "done"],
            )

        entries = journal.to_list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["idempotency_key"] == "je_cmd_1"
        assert entry["fn_name"] == "subprocess_exec"
        assert entry["status"] == "success"
        assert isinstance(entry["result"], dict)
        assert "exit_code" in entry["result"]
        assert "stdout" in entry["result"]

    def test_multiple_boundary_types_coexist_in_journal(self) -> None:
        journal = ActivityJournal()

        # HTTP activity
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.headers = {}
        with patch("veridian.loop.activity_boundary.httpx") as mock_httpx:
            mock_httpx.request.return_value = mock_response
            http_activity(
                journal=journal,
                activity_id="multi_http",
                method="GET",
                url="https://example.com",
            )

        # File activity
        with patch("veridian.loop.activity_boundary.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.stat.return_value = MagicMock(st_size=10)
            MockPath.return_value = mock_path
            file_activity(
                journal=journal,
                activity_id="multi_file",
                path="/tmp/x",
                operation="exists",
            )

        # Subprocess activity
        with patch("veridian.loop.activity_boundary.subprocess") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_sub.run.return_value = mock_proc
            subprocess_activity(
                journal=journal,
                activity_id="multi_cmd",
                cmd=["true"],
            )

        entries = journal.to_list()
        assert len(entries) == 3
        fn_names = {e["fn_name"] for e in entries}
        assert fn_names == {"http_request", "file_check", "subprocess_exec"}
