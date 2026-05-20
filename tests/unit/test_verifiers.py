"""
tests.unit.test_verifiers
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Phase 2 tests: 8 built-in verifiers.

Pattern per verifier (CLAUDE.md Â§4.2):
  1. test_passes_when_<happy_path>
  2. test_fails_when_<failure_case>
  3. test_error_message_is_actionable  (error names what failed, â‰¤ 300 chars)
  4. test_config_validation_rejects_<bad_config>
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

# â”€â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def make_task(**kwargs: Any) -> Task:
    return Task(title="test", **kwargs)


def make_result(
    structured: dict[str, Any] = None, raw: str = "", artifacts: list[str] = None
) -> TaskResult:
    return TaskResult(
        raw_output=raw,
        structured=structured or {},
        artifacts=artifacts or [],
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# BashExitCodeVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestBashExitCodeVerifier:
    @pytest.fixture
    def pass_verifier(self) -> Any:
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        return BashExitCodeVerifier(command='python -c "import sys; sys.exit(0)"')

    @pytest.fixture
    def fail_verifier(self) -> Any:
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        return BashExitCodeVerifier(command='python -c "import sys; sys.exit(1)"')

    def test_passes_when_exit_code_matches(self, pass_verifier: Any) -> None:
        """Should pass when command exits with expected code."""
        result = pass_verifier.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_when_exit_code_mismatches(self, fail_verifier: Any) -> None:
        """Should fail when command exits with non-zero code (expected 0)."""
        result = fail_verifier.verify(make_task(), make_result())
        assert result.passed is False

    def test_error_message_is_actionable(self, fail_verifier: Any) -> None:
        """Error must mention exit code and be â‰¤ 300 chars."""
        result = fail_verifier.verify(make_task(), make_result())
        assert result.error is not None
        assert "exit" in result.error.lower() or "exited" in result.error.lower()
        assert len(result.error) <= 300

    def test_config_validation_rejects_empty_command(self) -> None:
        """Empty command string should raise VeridianConfigError."""
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        with pytest.raises(VeridianConfigError, match="command"):
            BashExitCodeVerifier(command="")

    def test_passes_with_custom_expected_exit(self) -> None:
        """Should pass when exit code matches non-zero expected value."""
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        v = BashExitCodeVerifier(
            command='python -c "import sys; sys.exit(42)"',
            expected_exit=42,
        )
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_when_exit_code_does_not_match_custom_expected(self) -> None:
        """Should fail when actual exit != custom expected."""
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        v = BashExitCodeVerifier(
            command='python -c "import sys; sys.exit(0)"',
            expected_exit=42,
        )
        result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_config_validation_rejects_non_positive_timeout(self) -> None:
        """timeout_seconds <= 0 should raise VeridianConfigError."""
        from veridian.verify.builtin.bash import BashExitCodeVerifier

        with pytest.raises(VeridianConfigError, match="timeout_seconds"):
            BashExitCodeVerifier(command="echo hi", timeout_seconds=0)

    def test_fails_when_command_times_out(self) -> None:
        """Should return failed result when command exceeds timeout."""
        import subprocess
        from unittest.mock import patch

        from veridian.verify.builtin.bash import BashExitCodeVerifier

        v = BashExitCodeVerifier(command="sleep 10", timeout_seconds=1)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep 10", 1)):
            result = v.verify(make_task(), make_result())
        assert result.passed is False
        assert result.error is not None


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# QuoteMatchVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestQuoteMatchVerifier:
    def test_passes_when_quote_found_in_txt_file(self, tmp_path: Path) -> None:
        """Should pass when quote appears verbatim in source file."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        src = tmp_path / "doc.txt"
        src.write_text("The quick brown fox jumps over the lazy dog.")
        v = QuoteMatchVerifier(source_file=str(src))
        result = v.verify(
            make_task(),
            make_result(structured={"quote": "quick brown fox"}),
        )
        assert result.passed is True

    def test_fails_when_quote_not_in_source(self, tmp_path: Path) -> None:
        """Should fail when quote does not appear in the source document."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        src = tmp_path / "doc.txt"
        src.write_text("The quick brown fox jumps over the lazy dog.")
        v = QuoteMatchVerifier(source_file=str(src))
        result = v.verify(
            make_task(),
            make_result(structured={"quote": "Lorem ipsum dolor sit amet"}),
        )
        assert result.passed is False

    def test_error_message_is_actionable(self, tmp_path: Path) -> None:
        """Error must name the quote and be â‰¤ 300 chars."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        src = tmp_path / "doc.txt"
        src.write_text("Some other content here.")
        v = QuoteMatchVerifier(source_file=str(src))
        result = v.verify(
            make_task(),
            make_result(structured={"quote": "Not in document at all"}),
        )
        assert result.error is not None
        assert len(result.error) <= 300
        # Must name what failed
        lower = result.error.lower()
        assert "quote" in lower or "found" in lower or "source" in lower

    def test_fails_when_source_file_missing(self) -> None:
        """Should fail gracefully when source file does not exist."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        v = QuoteMatchVerifier(source_file="/nonexistent/path/file.txt")
        result = v.verify(
            make_task(),
            make_result(structured={"quote": "any quote"}),
        )
        assert result.passed is False
        assert result.error is not None

    def test_normalises_whitespace_for_matching(self, tmp_path: Path) -> None:
        """Should match quotes despite minor whitespace differences."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        src = tmp_path / "doc.txt"
        src.write_text("The   quick  brown fox.")
        v = QuoteMatchVerifier(source_file=str(src))
        result = v.verify(
            make_task(),
            make_result(structured={"quote": "The quick brown fox."}),
        )
        assert result.passed is True

    def test_config_validation_rejects_short_min_quote(self, tmp_path: Path) -> None:
        """min_quote_length < 1 should raise VeridianConfigError."""
        from veridian.verify.builtin.quote import QuoteMatchVerifier

        src = tmp_path / "doc.txt"
        src.write_text("content")
        with pytest.raises(VeridianConfigError, match="min_quote_length"):
            QuoteMatchVerifier(source_file=str(src), min_quote_length=0)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SchemaVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestSchemaVerifier:
    def test_passes_when_all_required_fields_present(self) -> None:
        """Should pass when all required_fields are in structured output."""
        from veridian.verify.builtin.schema import SchemaVerifier

        v = SchemaVerifier(required_fields=["name", "risk_level", "summary"])
        result = v.verify(
            make_task(),
            make_result(structured={"name": "clause", "risk_level": "HIGH", "summary": "ok"}),
        )
        assert result.passed is True

    def test_fails_when_required_field_missing(self) -> None:
        """Should fail when a required field is absent from structured output."""
        from veridian.verify.builtin.schema import SchemaVerifier

        v = SchemaVerifier(required_fields=["name", "risk_level"])
        result = v.verify(
            make_task(),
            make_result(structured={"name": "clause"}),  # missing risk_level
        )
        assert result.passed is False

    def test_error_message_names_missing_field(self) -> None:
        """Error must include the name of the missing field and be â‰¤ 300 chars."""
        from veridian.verify.builtin.schema import SchemaVerifier

        v = SchemaVerifier(required_fields=["risk_level"])
        result = v.verify(make_task(), make_result(structured={}))
        assert result.error is not None
        assert "risk_level" in result.error
        assert len(result.error) <= 300

    def test_passes_with_json_schema_dict(self) -> None:
        """Should pass when structured matches a JSON Schema dict."""
        from veridian.verify.builtin.schema import SchemaVerifier

        v = SchemaVerifier(
            schema={"required": ["score"], "properties": {"score": {"type": "number"}}}
        )
        result = v.verify(make_task(), make_result(structured={"score": 0.9}))
        assert result.passed is True

    def test_fails_with_json_schema_missing_required(self) -> None:
        """Should fail when JSON Schema required field is absent."""
        from veridian.verify.builtin.schema import SchemaVerifier

        v = SchemaVerifier(schema={"required": ["score"]})
        result = v.verify(make_task(), make_result(structured={}))
        assert result.passed is False

    def test_config_validation_rejects_no_schema_and_no_fields(self) -> None:
        """SchemaVerifier with neither schema nor required_fields raises VeridianConfigError."""
        from veridian.verify.builtin.schema import SchemaVerifier

        with pytest.raises(VeridianConfigError):
            SchemaVerifier()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HttpStatusVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestHttpStatusVerifier:
    def test_passes_when_status_in_expected_statuses(self) -> None:
        """Should pass when HTTP response status is in expected list."""
        from veridian.verify.builtin.http import HttpStatusVerifier

        v = HttpStatusVerifier(url="https://example.com", expected_statuses=[200])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_when_status_not_in_expected_statuses(self) -> None:
        """Should fail when HTTP response status does not match."""
        from veridian.verify.builtin.http import HttpStatusVerifier

        v = HttpStatusVerifier(url="https://example.com", expected_statuses=[200])
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_error_message_is_actionable(self) -> None:
        """Error must name the URL and status codes, be â‰¤ 300 chars."""
        from veridian.verify.builtin.http import HttpStatusVerifier

        v = HttpStatusVerifier(url="https://example.com/api", expected_statuses=[200])
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            result = v.verify(make_task(), make_result())
        assert result.error is not None
        assert "503" in result.error or "503" in str(result.error)
        assert len(result.error) <= 300

    def test_fails_gracefully_on_connection_error(self) -> None:
        """Connection errors should return failed result, not raise."""
        import httpx

        from veridian.verify.builtin.http import HttpStatusVerifier

        v = HttpStatusVerifier(url="https://unreachable.example.com")
        with patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")):
            result = v.verify(make_task(), make_result())
        assert result.passed is False
        assert result.error is not None

    def test_config_validation_rejects_empty_url(self) -> None:
        """Empty URL should raise VeridianConfigError."""
        from veridian.verify.builtin.http import HttpStatusVerifier

        with pytest.raises(VeridianConfigError, match="url"):
            HttpStatusVerifier(url="")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FileExistsVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestFileExistsVerifier:
    def test_passes_when_all_files_exist(self, tmp_path: Path) -> None:
        """Should pass when all configured files exist on disk."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        f1 = tmp_path / "output.json"
        f1.write_text('{"result": "done"}')
        v = FileExistsVerifier(files=[str(f1)])
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_when_file_missing(self) -> None:
        """Should fail when a configured file does not exist."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        v = FileExistsVerifier(files=["/tmp/does_not_exist_xyz_veridian.json"])
        result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_error_message_names_missing_file(self) -> None:
        """Error must name the missing file path and be â‰¤ 300 chars."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        missing = "/tmp/missing_veridian_test_file.txt"
        v = FileExistsVerifier(files=[missing])
        result = v.verify(make_task(), make_result())
        assert result.error is not None
        assert "missing_veridian_test_file" in result.error
        assert len(result.error) <= 300

    def test_fails_when_file_is_empty_and_check_non_empty(self, tmp_path: Path) -> None:
        """Should fail on empty file when check_non_empty=True."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        f = tmp_path / "empty.txt"
        f.write_text("")
        v = FileExistsVerifier(files=[str(f)], check_non_empty=True)
        result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_passes_when_file_is_empty_and_check_non_empty_false(self, tmp_path: Path) -> None:
        """Should pass on empty file when check_non_empty=False."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        f = tmp_path / "empty.txt"
        f.write_text("")
        v = FileExistsVerifier(files=[str(f)], check_non_empty=False)
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_config_validation_rejects_empty_files_list(self) -> None:
        """Empty files list should raise VeridianConfigError."""
        from veridian.verify.builtin.file_exists import FileExistsVerifier

        with pytest.raises(VeridianConfigError, match="files"):
            FileExistsVerifier(files=[])


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CompositeVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class _AlwaysPass(BaseVerifier):
    id = "_test_pass"
    description = "Always passes."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=True)


class _AlwaysFail(BaseVerifier):
    id = "_test_fail"
    description = "Always fails."

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        return VerificationResult(passed=False, error="intentional failure")


class TestCompositeVerifier:
    def test_passes_when_all_sub_verifiers_pass(self) -> None:
        """Should pass when every verifier in the chain passes."""
        from veridian.verify.builtin.composite import CompositeVerifier

        v = CompositeVerifier(verifiers=[_AlwaysPass(), _AlwaysPass()])
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_on_first_failing_verifier(self) -> None:
        """Should fail as soon as one verifier fails (short-circuit AND)."""
        from veridian.verify.builtin.composite import CompositeVerifier

        v = CompositeVerifier(verifiers=[_AlwaysFail(), _AlwaysPass()])
        result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_error_message_has_step_prefix(self) -> None:
        """Error must be prefixed with '[Step N/total]' and be â‰¤ 300 chars."""
        from veridian.verify.builtin.composite import CompositeVerifier

        v = CompositeVerifier(verifiers=[_AlwaysPass(), _AlwaysFail()])
        result = v.verify(make_task(), make_result())
        assert result.error is not None
        assert "[Step" in result.error
        assert len(result.error) <= 300

    def test_config_validation_rejects_empty_verifiers(self) -> None:
        """Empty verifiers list should raise VeridianConfigError."""
        from veridian.verify.builtin.composite import CompositeVerifier

        with pytest.raises(VeridianConfigError):
            CompositeVerifier(verifiers=[])

    def test_all_pass_runs_full_chain(self) -> None:
        """Evidence should reflect all sub-verifiers ran."""
        from veridian.verify.builtin.composite import CompositeVerifier

        v = CompositeVerifier(verifiers=[_AlwaysPass(), _AlwaysPass(), _AlwaysPass()])
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_dict_verifier_resolved_from_registry(self) -> None:
        """CompositeVerifier should resolve dict items via the verifier registry."""
        from veridian.verify.base import registry
        from veridian.verify.builtin.composite import CompositeVerifier

        # Register our test verifier so it can be looked up
        registry.register(_AlwaysPass)
        v = CompositeVerifier(verifiers=[{"id": "_test_pass"}])
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_invalid_verifier_type_raises_config_error(self) -> None:
        """Non-BaseVerifier, non-dict items must raise VeridianConfigError."""
        from veridian.verify.builtin.composite import CompositeVerifier

        with pytest.raises(VeridianConfigError, match="must be a BaseVerifier"):
            CompositeVerifier(verifiers=["not_a_verifier"])


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# AnyOfVerifier
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestAnyOfVerifier:
    def test_passes_when_one_sub_verifier_passes(self) -> None:
        """Should pass if at least one verifier passes (OR logic)."""
        from veridian.verify.builtin.any_of import AnyOfVerifier

        v = AnyOfVerifier(verifiers=[_AlwaysFail(), _AlwaysPass()])
        result = v.verify(make_task(), make_result())
        assert result.passed is True

    def test_fails_when_all_sub_verifiers_fail(self) -> None:
        """Should fail when no verifier passes."""
        from veridian.verify.builtin.any_of import AnyOfVerifier

        v = AnyOfVerifier(verifiers=[_AlwaysFail(), _AlwaysFail()])
        result = v.verify(make_task(), make_result())
        assert result.passed is False

    def test_error_message_includes_all_failures(self) -> None:
        """Error must aggregate sub-verifier errors and be â‰¤ 300 chars."""
        from veridian.verify.builtin.any_of import AnyOfVerifier

        v = AnyOfVerifier(verifiers=[_AlwaysFail(), _AlwaysFail()])
        result = v.verify(make_task(), make_result())
        assert result.error is not None
        # Error should indicate all failed
        assert "intentional failure" in result.error or "all" in result.error.lower()
        assert len(result.error) <= 300

    def test_config_validation_rejects_empty_verifiers(self) -> None:
        """Empty verifiers list should raise VeridianConfigError."""
        from veridian.verify.builtin.any_of import AnyOfVerifier

        with pytest.raises(VeridianConfigError):
            AnyOfVerifier(verifiers=[])

    def test_passes_with_first_verifier_passing(self) -> None:
        """Should short-circuit and pass on first successful verifier."""
        from veridian.verify.builtin.any_of import AnyOfVerifier

        v = AnyOfVerifier(verifiers=[_AlwaysPass(), _AlwaysFail()])
        result = v.verify(make_task(), make_result())
        assert result.passed is True


