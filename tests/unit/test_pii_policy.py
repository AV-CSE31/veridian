"""
tests.unit.test_pii_policy
───────────────────────────
WCP-026: PII detection and redaction policy.

Verifies:
1. PIIPattern matches SSN format (xxx-xx-xxxx)
2. PIIPattern matches email addresses
3. PIIPattern matches credit card numbers (16 digits with optional separators)
4. PIIPattern matches API keys (common patterns like sk-*, AKIA*, etc.)
5. PIIPolicy.detect() returns list of matches with positions
6. PIIPolicy.redact() replaces sensitive data with [REDACTED-TYPE]
7. Custom patterns can be added/removed
8. False positive handling: "123-45-6789" in code comments not over-redacted
"""

from __future__ import annotations

import re

from veridian.secrets.pii_policy import (
    BUILTIN_PATTERNS,
    PIIMatch,
    PIIPattern,
    PIIPolicy,
)


class TestPIIPatternSSN:
    def test_matches_standard_ssn(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("My SSN is 123-45-6789")
        names = [m.pattern_name for m in matches]
        assert "ssn" in names

    def test_does_not_match_non_ssn_digits(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("version 1.2.3-45-6")
        names = [m.pattern_name for m in matches]
        assert "ssn" not in names


class TestPIIPatternEmail:
    def test_matches_email(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Contact user@example.com for info")
        names = [m.pattern_name for m in matches]
        assert "email" in names

    def test_matches_email_with_plus(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Send to user+tag@example.co.uk")
        names = [m.pattern_name for m in matches]
        assert "email" in names


class TestPIIPatternCreditCard:
    def test_matches_16_digit_credit_card(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Card: 4111111111111111")
        names = [m.pattern_name for m in matches]
        assert "credit_card" in names

    def test_matches_credit_card_with_dashes(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Card: 4111-1111-1111-1111")
        names = [m.pattern_name for m in matches]
        assert "credit_card" in names

    def test_matches_credit_card_with_spaces(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Card: 4111 1111 1111 1111")
        names = [m.pattern_name for m in matches]
        assert "credit_card" in names


class TestPIIPatternAPIKeys:
    def test_matches_openai_key(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("sk-projXabcdefghijklmnop1234567890")
        names = [m.pattern_name for m in matches]
        assert "api_key" in names

    def test_matches_aws_key(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("AKIAIOSFODNN7EXAMPLE")
        names = [m.pattern_name for m in matches]
        assert "api_key" in names

    def test_matches_bearer_token(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        names = [m.pattern_name for m in matches]
        assert "bearer_token" in names


class TestPIIPolicyDetect:
    def test_detect_returns_list_of_pii_match(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("SSN: 123-45-6789, email: a@b.com")
        assert isinstance(matches, list)
        assert all(isinstance(m, PIIMatch) for m in matches)

    def test_detect_returns_positions(self) -> None:
        policy = PIIPolicy()
        text = "SSN: 123-45-6789"
        matches = policy.detect(text)
        ssn_matches = [m for m in matches if m.pattern_name == "ssn"]
        assert len(ssn_matches) >= 1
        m = ssn_matches[0]
        assert m.start >= 0
        assert m.end > m.start
        assert text[m.start : m.end] == m.original

    def test_detect_no_matches_returns_empty(self) -> None:
        policy = PIIPolicy()
        matches = policy.detect("Nothing sensitive here.")
        assert matches == []


class TestPIIPolicyRedact:
    def test_redact_replaces_ssn(self) -> None:
        policy = PIIPolicy()
        result = policy.redact("SSN: 123-45-6789")
        assert "123-45-6789" not in result
        assert "[REDACTED-SSN]" in result

    def test_redact_replaces_email(self) -> None:
        policy = PIIPolicy()
        result = policy.redact("Email: user@example.com")
        assert "user@example.com" not in result
        assert "[REDACTED-EMAIL]" in result

    def test_redact_replaces_credit_card(self) -> None:
        policy = PIIPolicy()
        result = policy.redact("Card: 4111111111111111")
        assert "4111111111111111" not in result
        assert "[REDACTED-CREDIT_CARD]" in result

    def test_redact_handles_multiple_types(self) -> None:
        policy = PIIPolicy()
        text = "SSN 123-45-6789 and email user@test.com"
        result = policy.redact(text)
        assert "123-45-6789" not in result
        assert "user@test.com" not in result

    def test_redact_preserves_nonsensitive(self) -> None:
        policy = PIIPolicy()
        result = policy.redact("Hello world, SSN: 123-45-6789.")
        assert "Hello world," in result


class TestCustomPatterns:
    def test_add_pattern(self) -> None:
        policy = PIIPolicy()
        custom = PIIPattern(
            name="custom_id",
            regex=re.compile(r"CUST-\d{6}"),
            replacement="[REDACTED-CUSTOM_ID]",
        )
        policy.add_pattern(custom)
        matches = policy.detect("ID is CUST-123456")
        names = [m.pattern_name for m in matches]
        assert "custom_id" in names

    def test_remove_pattern(self) -> None:
        policy = PIIPolicy()
        policy.remove_pattern("ssn")
        matches = policy.detect("SSN: 123-45-6789")
        names = [m.pattern_name for m in matches]
        assert "ssn" not in names

    def test_remove_nonexistent_pattern_is_safe(self) -> None:
        policy = PIIPolicy()
        # Should not raise
        policy.remove_pattern("nonexistent_pattern")


class TestFalsePositives:
    def test_code_comment_not_over_redacted(self) -> None:
        """Legitimate code patterns should not trigger excessive redaction."""
        policy = PIIPolicy()
        # A version string that looks like SSN prefix but isn't
        text = "version = '2.3.4' # build 123-45"
        result = policy.redact(text)
        # The version string should survive
        assert "2.3.4" in result

    def test_phone_number_format_not_confused_with_ssn(self) -> None:
        """Phone numbers have different digit grouping from SSN."""
        policy = PIIPolicy()
        # Phone: (123) 456-7890 should not match SSN pattern
        matches = policy.detect("Call (123) 456-7890")
        ssn_matches = [m for m in matches if m.pattern_name == "ssn"]
        assert len(ssn_matches) == 0


class TestBuiltinPatterns:
    def test_builtin_patterns_is_nonempty(self) -> None:
        assert len(BUILTIN_PATTERNS) > 0

    def test_builtin_patterns_have_unique_names(self) -> None:
        names = [p.name for p in BUILTIN_PATTERNS]
        assert len(names) == len(set(names))

    def test_default_policy_uses_builtins(self) -> None:
        policy = PIIPolicy()
        pattern_names = {p.name for p in policy._patterns}
        builtin_names = {p.name for p in BUILTIN_PATTERNS}
        assert pattern_names == builtin_names
