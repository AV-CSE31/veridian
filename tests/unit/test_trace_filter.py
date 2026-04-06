"""
tests.unit.test_trace_filter
──────────────────────────────
WCP-026: Trace-level PII filtering.

Verifies:
1. TraceFilter redacts PII from flat event dict
2. TraceFilter handles nested dicts recursively
3. TraceFilter handles lists
4. TraceFilter preserves non-sensitive fields
5. filter_event() returns sanitized copy (original unchanged)
"""

from __future__ import annotations

from veridian.secrets.pii_policy import PIIPolicy
from veridian.secrets.trace_filter import TraceFilter


class TestFlatDict:
    def test_redacts_ssn_in_flat_event(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"user_ssn": "123-45-6789", "action": "login"}
        result = tf.filter_event(event)
        assert "123-45-6789" not in result["user_ssn"]
        assert "[REDACTED-SSN]" in result["user_ssn"]

    def test_redacts_email_in_flat_event(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"contact": "user@example.com"}
        result = tf.filter_event(event)
        assert "user@example.com" not in result["contact"]
        assert "[REDACTED-EMAIL]" in result["contact"]


class TestNestedDict:
    def test_redacts_pii_in_nested_dict(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {
            "metadata": {
                "user": {
                    "ssn": "123-45-6789",
                    "name": "Alice",
                },
            },
        }
        result = tf.filter_event(event)
        assert "123-45-6789" not in result["metadata"]["user"]["ssn"]
        assert result["metadata"]["user"]["name"] == "Alice"

    def test_handles_deeply_nested_dict(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"a": {"b": {"c": {"email": "deep@test.com"}}}}
        result = tf.filter_event(event)
        assert "deep@test.com" not in result["a"]["b"]["c"]["email"]


class TestListHandling:
    def test_redacts_pii_in_list_of_strings(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"emails": ["a@b.com", "c@d.com"]}
        result = tf.filter_event(event)
        assert all("@" not in str(v) for v in result["emails"])

    def test_redacts_pii_in_list_of_dicts(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {
            "records": [
                {"ssn": "111-22-3333"},
                {"ssn": "444-55-6666"},
            ],
        }
        result = tf.filter_event(event)
        for rec in result["records"]:
            assert "111-22-3333" not in rec["ssn"]
            assert "444-55-6666" not in rec["ssn"]


class TestPreservation:
    def test_preserves_non_sensitive_fields(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {
            "event_type": "task_start",
            "run_id": "run-001",
            "duration_ms": 42.5,
            "tags": ["safe", "test"],
        }
        result = tf.filter_event(event)
        assert result["event_type"] == "task_start"
        assert result["run_id"] == "run-001"
        assert result["duration_ms"] == 42.5
        assert result["tags"] == ["safe", "test"]

    def test_preserves_int_and_float_values(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"count": 7, "ratio": 3.14, "flag": True}
        result = tf.filter_event(event)
        assert result["count"] == 7
        assert result["ratio"] == 3.14
        assert result["flag"] is True

    def test_preserves_none_values(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        event = {"optional": None}
        result = tf.filter_event(event)
        assert result["optional"] is None


class TestImmutability:
    def test_original_event_is_unchanged(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        original = {"ssn": "123-45-6789", "name": "Alice"}
        _ = tf.filter_event(original)
        assert original["ssn"] == "123-45-6789"
        assert original["name"] == "Alice"

    def test_nested_original_unchanged(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        original = {"meta": {"email": "user@test.com"}}
        _ = tf.filter_event(original)
        assert original["meta"]["email"] == "user@test.com"

    def test_returns_new_dict(self) -> None:
        policy = PIIPolicy()
        tf = TraceFilter(policy=policy)
        original = {"key": "value"}
        result = tf.filter_event(original)
        assert result is not original
