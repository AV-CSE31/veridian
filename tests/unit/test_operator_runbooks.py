"""
tests.unit.test_operator_runbooks
───────────────────────────────────
Unit tests for operator runbooks.

Proves:
- Register a runbook, lookup by symptom, list all
- Built-in runbooks are pre-registered
- Lookup returns relevant matches
"""

from __future__ import annotations

from veridian.operator.runbooks import Runbook, RunbookRegistry

# ── Registration ─────────────────────────────────────────────────────────────


class TestRegister:
    def test_register_and_list(self) -> None:
        reg = RunbookRegistry()
        rb = Runbook(
            title="test-runbook",
            symptoms=["error 500"],
            diagnosis_steps=["check logs"],
            resolution_steps=["restart service"],
            escalation="page SRE",
        )
        reg.register(rb)
        assert len(reg.list_all()) == 1
        assert reg.list_all()[0].title == "test-runbook"

    def test_register_multiple(self) -> None:
        reg = RunbookRegistry()
        for i in range(3):
            rb = Runbook(
                title=f"runbook-{i}",
                symptoms=[f"symptom-{i}"],
                diagnosis_steps=["step"],
                resolution_steps=["fix"],
                escalation="escalate",
            )
            reg.register(rb)
        assert len(reg.list_all()) == 3


# ── Lookup by symptom ────────────────────────────────────────────────────────


class TestLookupBySymptom:
    def test_finds_matching_symptom(self) -> None:
        reg = RunbookRegistry()
        rb = Runbook(
            title="timeout-fix",
            symptoms=["task stuck in IN_PROGRESS", "timeout"],
            diagnosis_steps=["check duration"],
            resolution_steps=["kill and retry"],
            escalation="contact ops",
        )
        reg.register(rb)
        matches = reg.lookup_by_symptom("stuck")
        assert len(matches) == 1
        assert matches[0].title == "timeout-fix"

    def test_no_match_returns_empty(self) -> None:
        reg = RunbookRegistry()
        rb = Runbook(
            title="some-runbook",
            symptoms=["specific symptom"],
            diagnosis_steps=["step"],
            resolution_steps=["fix"],
            escalation="escalate",
        )
        reg.register(rb)
        matches = reg.lookup_by_symptom("completely unrelated")
        assert len(matches) == 0

    def test_case_insensitive_match(self) -> None:
        reg = RunbookRegistry()
        rb = Runbook(
            title="cost-runbook",
            symptoms=["Budget exceeded"],
            diagnosis_steps=["check budget"],
            resolution_steps=["increase limit"],
            escalation="contact finance",
        )
        reg.register(rb)
        matches = reg.lookup_by_symptom("budget")
        assert len(matches) == 1


# ── Built-in runbooks ───────────────────────────────────────────────────────


class TestBuiltinRunbooks:
    def test_builtin_count(self) -> None:
        reg = RunbookRegistry.with_builtins()
        assert len(reg.list_all()) == 4

    def test_stuck_task_runbook(self) -> None:
        reg = RunbookRegistry.with_builtins()
        matches = reg.lookup_by_symptom("stuck")
        assert len(matches) >= 1

    def test_cost_overrun_runbook(self) -> None:
        reg = RunbookRegistry.with_builtins()
        matches = reg.lookup_by_symptom("cost")
        assert len(matches) >= 1

    def test_provider_failure_runbook(self) -> None:
        reg = RunbookRegistry.with_builtins()
        matches = reg.lookup_by_symptom("provider")
        assert len(matches) >= 1

    def test_verification_loop_runbook(self) -> None:
        reg = RunbookRegistry.with_builtins()
        matches = reg.lookup_by_symptom("verification loop")
        assert len(matches) >= 1
