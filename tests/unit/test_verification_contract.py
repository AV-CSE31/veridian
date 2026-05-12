"""
tests.unit.test_verification_contract
─────────────────────────────────────
Tests for the core ``VerificationContract`` dataclass.

Covers:
- Construction defaults and field validation.
- Failure-mode allowlist enforcement.
- Retry/fatal/advisory invariants.
- JSON-serializable verifier_config enforcement.
- Fingerprint determinism and sensitivity to field changes.
- to_dict / from_dict round-trip.
- with_policy_version returns a new instance with the field replaced.
"""

from __future__ import annotations

import pytest

from veridian.core.contract import FAILURE_MODES, VerificationContract
from veridian.core.exceptions import VeridianConfigError


def _mk(**overrides: object) -> VerificationContract:
    base: dict[str, object] = {
        "verifier_id": "schema",
        "verifier_config": {"required_fields": ["decision"]},
        "policy_version": "v1.0.0",
    }
    base.update(overrides)
    return VerificationContract(**base)  # type: ignore[arg-type]


class TestConstruction:
    def test_minimal_construction(self) -> None:
        c = VerificationContract(verifier_id="schema")
        assert c.verifier_id == "schema"
        assert c.policy_version == "unversioned"
        assert c.fatal is True
        assert c.retryable is False
        assert c.on_failure == "raise"
        assert c.deterministic is True

    def test_full_construction(self) -> None:
        c = _mk(retryable=True, max_retries=3, on_failure="retry")
        assert c.verifier_id == "schema"
        assert c.policy_version == "v1.0.0"
        assert c.retryable is True
        assert c.max_retries == 3
        assert c.on_failure == "retry"

    def test_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        c = _mk()
        with pytest.raises(FrozenInstanceError):
            c.verifier_id = "other"  # type: ignore[misc]


class TestValidation:
    def test_empty_verifier_id_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="verifier_id"):
            VerificationContract(verifier_id="")

    def test_empty_policy_version_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="policy_version"):
            _mk(policy_version="")

    def test_invalid_failure_mode_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="on_failure"):
            _mk(on_failure="explode")

    def test_all_failure_modes_accepted(self) -> None:
        for mode in FAILURE_MODES:
            _mk(on_failure=mode)  # should not raise

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="max_retries"):
            _mk(max_retries=-1)

    def test_max_retries_without_retryable_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="retryable=False"):
            _mk(retryable=False, max_retries=2)

    def test_retryable_with_zero_retries_allowed(self) -> None:
        c = _mk(retryable=True, max_retries=0)
        assert c.retryable is True

    def test_advisory_must_use_raise_failure_mode(self) -> None:
        with pytest.raises(VeridianConfigError, match="advisory"):
            _mk(fatal=False, on_failure="dlq")

    def test_advisory_with_default_failure_mode_allowed(self) -> None:
        c = _mk(fatal=False)
        assert c.fatal is False

    def test_non_json_serializable_config_rejected(self) -> None:
        with pytest.raises(VeridianConfigError, match="JSON"):
            _mk(verifier_config={"set": {1, 2, 3}})


class TestFingerprint:
    def test_fingerprint_is_stable_across_instances(self) -> None:
        a = _mk()
        b = _mk()
        assert a.fingerprint() == b.fingerprint()

    def test_fingerprint_length(self) -> None:
        assert len(_mk().fingerprint()) == 16

    def test_fingerprint_changes_on_verifier_id(self) -> None:
        a = _mk()
        b = _mk(verifier_id="bash_exit")
        assert a.fingerprint() != b.fingerprint()

    def test_fingerprint_changes_on_policy_version(self) -> None:
        a = _mk()
        b = _mk(policy_version="v2.0.0")
        assert a.fingerprint() != b.fingerprint()

    def test_fingerprint_changes_on_config(self) -> None:
        a = _mk(verifier_config={"x": 1})
        b = _mk(verifier_config={"x": 2})
        assert a.fingerprint() != b.fingerprint()

    def test_fingerprint_is_config_order_insensitive(self) -> None:
        a = _mk(verifier_config={"a": 1, "b": 2})
        b = _mk(verifier_config={"b": 2, "a": 1})
        assert a.fingerprint() == b.fingerprint()


class TestSerialization:
    def test_to_dict_round_trip(self) -> None:
        original = _mk(retryable=True, max_retries=2, on_failure="retry")
        restored = VerificationContract.from_dict(original.to_dict())
        assert restored == original
        assert restored.fingerprint() == original.fingerprint()

    def test_to_dict_is_json_safe(self) -> None:
        import json

        c = _mk()
        json.dumps(c.to_dict())  # should not raise

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = _mk().to_dict()
        data["future_field"] = "ignored"
        restored = VerificationContract.from_dict(data)
        assert restored.verifier_id == "schema"

    def test_from_dict_requires_verifier_id(self) -> None:
        with pytest.raises(VeridianConfigError, match="verifier_id"):
            VerificationContract.from_dict({"policy_version": "v1.0.0"})


class TestErgonomics:
    def test_with_policy_version_returns_new_instance(self) -> None:
        original = _mk(policy_version="v1.0.0")
        bumped = original.with_policy_version("v2.0.0")
        assert original.policy_version == "v1.0.0"
        assert bumped.policy_version == "v2.0.0"
        assert bumped.fingerprint() != original.fingerprint()
