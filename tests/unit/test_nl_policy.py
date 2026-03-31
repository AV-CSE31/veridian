"""
tests.unit.test_nl_policy
──────────────────────────
Natural Language Policy Interface — NL → Policy-as-Code translation,
human review step, policy explanation, policy store.
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import NLPolicyError, PolicyNotFound
from veridian.policy.nl_interface import (
    NLPolicyInterface,
    PolicyCheck,
    PolicyDraft,
    PolicySeverity,
    PolicySpec,
    PolicyStatus,
    PolicyStore,
)

# ── PolicyCheck ───────────────────────────────────────────────────────────────


class TestPolicyCheck:
    def test_construct(self) -> None:
        check = PolicyCheck(field="output.pii", operator="not_contains", value="SSN")
        assert check.field == "output.pii"
        assert check.operator == "not_contains"

    def test_serialise_round_trip(self) -> None:
        check = PolicyCheck(field="output.url", operator="matches", value=r"^https://")
        d = check.to_dict()
        check2 = PolicyCheck.from_dict(d)
        assert check2.field == check.field
        assert check2.operator == check.operator
        assert check2.value == check.value


# ── PolicySpec ────────────────────────────────────────────────────────────────


class TestPolicySpec:
    def test_construct_minimal(self) -> None:
        spec = PolicySpec(
            rule_id="no_pii",
            description="No PII in output",
            checks=[PolicyCheck("output.text", "not_contains", "SSN")],
            severity=PolicySeverity.BLOCKING,
        )
        assert spec.rule_id == "no_pii"
        assert spec.severity == PolicySeverity.BLOCKING

    def test_to_yaml(self) -> None:
        spec = PolicySpec(
            rule_id="no_pii",
            description="No PII in output",
            checks=[PolicyCheck("output.text", "not_contains", "SSN")],
            severity=PolicySeverity.WARNING,
        )
        yaml_str = spec.to_yaml()
        assert "no_pii" in yaml_str
        assert "not_contains" in yaml_str
        assert "SSN" in yaml_str

    def test_from_yaml(self) -> None:
        yaml_str = (
            "rule_id: no_pii\n"
            "description: No PII in output\n"
            "severity: blocking\n"
            "checks:\n"
            "  - field: output.text\n"
            "    operator: not_contains\n"
            "    value: SSN\n"
        )
        spec = PolicySpec.from_yaml(yaml_str)
        assert spec.rule_id == "no_pii"
        assert len(spec.checks) == 1

    def test_serialise_round_trip(self) -> None:
        spec = PolicySpec(
            rule_id="r1",
            description="test",
            checks=[PolicyCheck("f", "eq", "v")],
            severity=PolicySeverity.BLOCKING,
        )
        d = spec.to_dict()
        spec2 = PolicySpec.from_dict(d)
        assert spec2.rule_id == spec.rule_id
        assert len(spec2.checks) == len(spec.checks)


# ── PolicyDraft ───────────────────────────────────────────────────────────────


class TestPolicyDraft:
    def test_construct(self) -> None:
        spec = PolicySpec("r1", "desc", [], PolicySeverity.WARNING)
        draft = PolicyDraft(
            draft_id="d1",
            nl_input="No agent should access PII",
            spec=spec,
        )
        assert draft.status == PolicyStatus.PENDING_REVIEW
        assert draft.draft_id == "d1"

    def test_approve(self) -> None:
        spec = PolicySpec("r1", "desc", [], PolicySeverity.WARNING)
        draft = PolicyDraft("d1", "NL input", spec)
        draft.approve()
        assert draft.status == PolicyStatus.ACTIVE

    def test_reject(self) -> None:
        spec = PolicySpec("r1", "desc", [], PolicySeverity.WARNING)
        draft = PolicyDraft("d1", "NL input", spec)
        draft.reject(reason="Incorrect interpretation")
        assert draft.status == PolicyStatus.REJECTED
        assert "Incorrect interpretation" in draft.rejection_reason

    def test_serialise_round_trip(self) -> None:
        spec = PolicySpec("r1", "desc", [PolicyCheck("f", "eq", "v")], PolicySeverity.BLOCKING)
        draft = PolicyDraft("d1", "No PII", spec)
        draft.approve()
        d = draft.to_dict()
        draft2 = PolicyDraft.from_dict(d)
        assert draft2.draft_id == draft.draft_id
        assert draft2.status == PolicyStatus.ACTIVE
        assert draft2.spec.rule_id == "r1"


# ── PolicyStore ───────────────────────────────────────────────────────────────


class TestPolicyStore:
    def test_save_and_load(self, tmp_path) -> None:
        store = PolicyStore(tmp_path / "policies.json")
        spec = PolicySpec("r1", "desc", [], PolicySeverity.WARNING)
        draft = PolicyDraft("d1", "NL input", spec)
        store.save(draft)
        loaded = store.get("d1")
        assert loaded.draft_id == "d1"

    def test_get_not_found_raises(self, tmp_path) -> None:
        store = PolicyStore(tmp_path / "policies.json")
        with pytest.raises(PolicyNotFound):
            store.get("nonexistent")

    def test_list_all(self, tmp_path) -> None:
        store = PolicyStore(tmp_path / "policies.json")
        for i in range(3):
            spec = PolicySpec(f"r{i}", "desc", [], PolicySeverity.WARNING)
            store.save(PolicyDraft(f"d{i}", "NL input", spec))
        assert len(store.list_all()) == 3

    def test_list_active(self, tmp_path) -> None:
        store = PolicyStore(tmp_path / "policies.json")
        for i in range(3):
            spec = PolicySpec(f"r{i}", "desc", [], PolicySeverity.WARNING)
            d = PolicyDraft(f"d{i}", "NL input", spec)
            if i == 0:
                d.approve()
            store.save(d)
        active = store.list_active()
        assert len(active) == 1
        assert active[0].draft_id == "d0"

    def test_atomic_write(self, tmp_path) -> None:
        import json
        path = tmp_path / "policies.json"
        store = PolicyStore(path)
        spec = PolicySpec("r1", "desc", [], PolicySeverity.WARNING)
        store.save(PolicyDraft("d1", "NL", spec))
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)


# ── NLPolicyInterface ─────────────────────────────────────────────────────────


class TestNLPolicyInterface:
    """
    NLPolicyInterface.translate() uses an LLM parser. In tests, we inject a
    mock translator so we don't need a real LLM.
    """

    def _make_interface(self, tmp_path) -> NLPolicyInterface:
        store = PolicyStore(tmp_path / "policies.json")
        # Mock translator: returns a fixed PolicySpec for any NL input
        def mock_translate(nl: str) -> PolicySpec:
            return PolicySpec(
                rule_id="auto_rule",
                description=nl[:50],
                checks=[PolicyCheck("output.text", "not_contains", "PII")],
                severity=PolicySeverity.BLOCKING,
            )
        return NLPolicyInterface(store, translator=mock_translate)

    def test_translate_returns_draft(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        draft = iface.translate("No agent should access customer PII without consent")
        assert isinstance(draft, PolicyDraft)
        assert draft.status == PolicyStatus.PENDING_REVIEW

    def test_draft_stored_after_translate(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        draft = iface.translate("No PII")
        store = iface._store
        loaded = store.get(draft.draft_id)
        assert loaded.draft_id == draft.draft_id

    def test_activate_pending_draft(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        draft = iface.translate("No PII")
        iface.activate(draft.draft_id)
        loaded = iface._store.get(draft.draft_id)
        assert loaded.status == PolicyStatus.ACTIVE

    def test_activate_nonexistent_raises(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        with pytest.raises(PolicyNotFound):
            iface.activate("nonexistent")

    def test_reject_draft(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        draft = iface.translate("No PII")
        iface.reject(draft.draft_id, reason="bad interpretation")
        loaded = iface._store.get(draft.draft_id)
        assert loaded.status == PolicyStatus.REJECTED

    def test_explain_returns_string(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        draft = iface.translate("No PII")
        explanation = iface.explain(draft.draft_id)
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_explain_nonexistent_raises(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        with pytest.raises(PolicyNotFound):
            iface.explain("nonexistent")

    def test_translate_generates_unique_ids(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        d1 = iface.translate("policy one")
        d2 = iface.translate("policy two")
        assert d1.draft_id != d2.draft_id

    def test_list_policies(self, tmp_path) -> None:
        iface = self._make_interface(tmp_path)
        iface.translate("policy one")
        iface.translate("policy two")
        all_policies = iface.list_policies()
        assert len(all_policies) == 2

    def test_default_translator_raises_without_llm(self, tmp_path) -> None:
        """NLPolicyInterface without a translator must raise NLPolicyError."""
        store = PolicyStore(tmp_path / "policies.json")
        iface = NLPolicyInterface(store)  # no translator injected
        with pytest.raises(NLPolicyError, match="translator"):
            iface.translate("some policy")
