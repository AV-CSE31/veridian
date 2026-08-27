"""Contract tests for the gate porcelain.

These lock the properties a caller is entitled to rely on: fail-closed
aggregation, exactly-one execution per permit, offline-verifiable proof, and a
policy digest that moves when the policy moves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from veridian.assurance import (
    ClauseSeverity,
    ClauseStatus,
    Disposition,
    Ed25519Signer,
    StaticKeyProvider,
    verify_proof_bundle,
)
from veridian.effects import EffectReceiptType, PermitError
from veridian.gate import (
    Check,
    CheckOutcome,
    Gate,
    GateConfigurationError,
    GateDeniedError,
    GateHeldError,
    check,
)


@check("amount_within_limit", config={"limit_minor": 100_000})
def amount_within_limit(ctx: object) -> bool:
    return int(ctx.parameters["amount_minor"]) <= 100_000  # type: ignore[attr-defined]


@check("recipient_allowlisted")
def recipient_allowlisted(ctx: object) -> bool:
    return str(ctx.parameters["to"]).startswith("acct:")  # type: ignore[attr-defined]


@check("always_unknown")
def always_unknown(ctx: object) -> CheckOutcome:
    return CheckOutcome(status=ClauseStatus.UNKNOWN, reason_code="FEED_UNAVAILABLE")


@check("soft_advisory", severity=ClauseSeverity.SOFT)
def soft_advisory(ctx: object) -> bool:
    return False


@check("explodes")
def explodes(ctx: object) -> bool:
    raise RuntimeError("upstream feed exploded")


def _gate(tmp_path: Path, *checks: Check, **kwargs: object) -> Gate:
    return Gate.for_development(
        audience="treasury-rail",
        checks=list(checks) or [amount_within_limit, recipient_allowlisted],
        store_path=tmp_path / "permits.db",
        **kwargs,  # type: ignore[arg-type]
    )


def _allow(gate: Gate) -> object:
    return gate.evaluate(
        action="payment.transfer",
        target="acct:1234",
        parameters={"to": "acct:1234", "amount_minor": 25_000},
    )


class TestDisposition:
    def test_all_hard_clauses_satisfied_allows_and_mints_a_permit(self, tmp_path: Path) -> None:
        verdict = _allow(_gate(tmp_path))
        assert verdict.disposition is Disposition.ALLOW
        assert verdict.allowed is True
        assert verdict.signed_permit is not None
        assert verdict.permit is not None
        assert verdict.permit.max_uses == 1

    def test_violated_hard_clause_denies_and_mints_no_permit(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        verdict = gate.evaluate(
            action="payment.transfer",
            target="acct:1234",
            parameters={"to": "acct:1234", "amount_minor": 999_999},
        )
        assert verdict.disposition is Disposition.DENY
        assert verdict.signed_permit is None
        assert "amount_within_limit" in verdict.reason()

    def test_unknown_hard_clause_holds_rather_than_allowing(self, tmp_path: Path) -> None:
        verdict = _gate(tmp_path, always_unknown).evaluate(
            action="payment.transfer", target="acct:1", parameters={}
        )
        assert verdict.disposition is Disposition.HOLD
        assert verdict.signed_permit is None

    def test_raising_check_becomes_error_and_never_passes(self, tmp_path: Path) -> None:
        verdict = _gate(tmp_path, explodes).evaluate(
            action="payment.transfer", target="acct:1", parameters={}
        )
        assert verdict.disposition is Disposition.HOLD
        clause = verdict.clause_results[0]
        assert clause.status is ClauseStatus.ERROR
        assert clause.reason_code == "CHECK_RAISED"
        assert clause.details["exception_type"] == "RuntimeError"

    def test_check_returning_non_boolean_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        sloppy = Check(clause_id="sloppy", predicate=lambda ctx: "yes")
        verdict = _gate(tmp_path, sloppy).evaluate(action="a", target="b", parameters={})
        assert verdict.clause_results[0].status is ClauseStatus.ERROR
        assert verdict.disposition is Disposition.HOLD

    def test_failing_soft_clause_alone_still_allows(self, tmp_path: Path) -> None:
        verdict = _gate(tmp_path, amount_within_limit, soft_advisory).evaluate(
            action="payment.transfer",
            target="acct:1",
            parameters={"amount_minor": 10},
        )
        assert verdict.disposition is Disposition.ALLOW
        assert verdict.failed_clauses()[0].clause_id == "soft_advisory"


class TestExecution:
    def test_guard_executes_the_body_and_attests_the_result(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        calls: list[str] = []

        @gate.guard("payment.transfer", target=lambda to, **_: to)
        def transfer(*, to: str, amount_minor: int) -> str:
            calls.append(to)
            return f"sent {amount_minor} to {to}"

        outcome = transfer(to="acct:1234", amount_minor=25_000)
        assert calls == ["acct:1234"]
        assert outcome.value == "sent 25000 to acct:1234"
        assert outcome.replayed is False
        assert outcome.receipt.receipt_type is EffectReceiptType.ACKNOWLEDGED

    def test_re_presenting_a_permit_replays_without_re_running_the_body(
        self, tmp_path: Path
    ) -> None:
        gate = _gate(tmp_path)
        verdict = _allow(gate)
        first = gate.execute(verdict, lambda: "effect")

        def must_not_run() -> str:
            raise AssertionError("a redeemed permit must never re-run the effect")

        second = gate.execute(verdict, must_not_run)
        assert first.replayed is False
        assert second.replayed is True
        assert second.receipt.digest == first.receipt.digest
        assert second.outbox_id == first.outbox_id

    def test_replay_survives_a_new_gate_over_the_same_store(self, tmp_path: Path) -> None:
        """Exactly-once is a property of the durable store, not of process memory."""
        signer = Ed25519Signer.generate("shared-key")
        keys = StaticKeyProvider.from_signers(signer)
        common = {
            "audience": "treasury-rail",
            "principal": "agent://test",
            "purpose": "payout",
            "checks": [amount_within_limit],
            "signer": signer,
            "permit_keys": keys,
            "receipt_keys": keys,
            "store_path": tmp_path / "shared.db",
        }
        first_gate = Gate(**common)  # type: ignore[arg-type]
        verdict = first_gate.evaluate(
            action="payment.transfer", target="acct:1", parameters={"amount_minor": 1}
        )
        first = first_gate.execute(verdict, lambda: "effect")

        second_gate = Gate(**common)  # type: ignore[arg-type]

        def must_not_run() -> str:
            raise AssertionError("replay must be durable across processes")

        second = second_gate.execute(verdict, must_not_run)
        assert second.replayed is True
        assert second.receipt.digest == first.receipt.digest

    def test_denied_verdict_cannot_be_executed(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        verdict = gate.evaluate(
            action="payment.transfer",
            target="acct:1",
            parameters={"to": "acct:1", "amount_minor": 999_999},
        )
        with pytest.raises(GateDeniedError):
            gate.execute(verdict, lambda: "effect")

    def test_guard_raises_denied_and_never_runs_the_body(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)

        @gate.guard("payment.transfer", target=lambda to, **_: to)
        def transfer(*, to: str, amount_minor: int) -> str:
            raise AssertionError("body must not run behind a denial")

        with pytest.raises(GateDeniedError) as caught:
            transfer(to="acct:1", amount_minor=999_999)
        assert caught.value.verdict is not None
        assert caught.value.verdict.disposition is Disposition.DENY

    def test_guard_raises_held_when_a_clause_cannot_decide(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path, always_unknown)

        @gate.guard("payment.transfer")
        def transfer(*, amount_minor: int) -> str:
            raise AssertionError("body must not run behind a hold")

        with pytest.raises(GateHeldError):
            transfer(amount_minor=1)


class TestProof:
    def test_proof_bundle_verifies_offline_against_the_gate_keys(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        verdict = _allow(gate)
        result = verify_proof_bundle(verdict.proof_bundle, gate.verification_keys)
        assert result.valid is True

    def test_proof_verification_does_not_overclaim_freshness_or_history(
        self, tmp_path: Path
    ) -> None:
        gate = _gate(tmp_path)
        result = verify_proof_bundle(_allow(gate).proof_bundle, gate.verification_keys)
        assert result.replay_status.value == "not-checked"
        assert result.history_status.value == "unanchored"

    def test_proof_bundle_fails_against_an_unrelated_key(self, tmp_path: Path) -> None:
        gate = _gate(tmp_path)
        stranger = StaticKeyProvider.from_signers(Ed25519Signer.generate("stranger"))
        result = verify_proof_bundle(_allow(gate).proof_bundle, stranger)
        assert result.valid is False


class TestPolicyIdentity:
    def test_changing_the_check_set_changes_the_contract_digest(self, tmp_path: Path) -> None:
        one = _gate(tmp_path / "a", amount_within_limit)
        two = _gate(tmp_path / "b", amount_within_limit, recipient_allowlisted)
        assert one.contract_digest != two.contract_digest

    def test_changing_a_check_version_changes_the_contract_digest(self, tmp_path: Path) -> None:
        original = Check(clause_id="c", predicate=lambda ctx: True, version="1.0.0")
        bumped = Check(clause_id="c", predicate=lambda ctx: True, version="2.0.0")
        assert (
            _gate(tmp_path / "a", original).contract_digest
            != _gate(tmp_path / "b", bumped).contract_digest
        )

    def test_changing_check_config_changes_the_manifest_digest(self) -> None:
        loose = Check(clause_id="c", predicate=lambda ctx: True, config={"limit": 1})
        tight = Check(clause_id="c", predicate=lambda ctx: True, config={"limit": 2})
        assert loose.manifest().digest != tight.manifest().digest

    def test_different_predicate_bodies_produce_different_manifests(self) -> None:
        def small(ctx: object) -> bool:
            return True

        def large(ctx: object) -> bool:
            return False

        assert (
            Check(clause_id="c", predicate=small).manifest().digest
            != Check(clause_id="c", predicate=large).manifest().digest
        )

    def test_manifest_declares_whether_source_was_bound(self) -> None:
        bound = Check(clause_id="c", predicate=amount_within_limit.predicate)
        assert bound.source_bound is True
        assert bound.manifest().config["source_bound"] is True

    def test_clause_result_carries_the_manifest_that_decided_it(self, tmp_path: Path) -> None:
        verdict = _allow(_gate(tmp_path, amount_within_limit))
        clause = verdict.clause_results[0]
        assert clause.verifier_manifest_digest == amount_within_limit.manifest().digest


class TestConfiguration:
    def test_a_gate_with_no_checks_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GateConfigurationError, match="at least one Check"):
            Gate.for_development(audience="a", checks=[], store_path=tmp_path / "p.db")

    def test_duplicate_clause_ids_are_refused(self, tmp_path: Path) -> None:
        duplicate = Check(clause_id="amount_within_limit", predicate=lambda ctx: True)
        with pytest.raises(GateConfigurationError, match="duplicate clause_id"):
            Gate.for_development(
                audience="a",
                checks=[amount_within_limit, duplicate],
                store_path=tmp_path / "p.db",
            )

    def test_non_check_entries_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GateConfigurationError, match="must contain veridian.gate.Check"):
            Gate.for_development(
                audience="a",
                checks=[lambda ctx: True],  # type: ignore[list-item]
                store_path=tmp_path / "p.db",
            )

    def test_non_positive_permit_ttl_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GateConfigurationError, match="permit_ttl_seconds"):
            Gate.for_development(
                audience="a",
                checks=[amount_within_limit],
                store_path=tmp_path / "p.db",
                permit_ttl_seconds=0,
            )

    def test_an_expired_permit_cannot_be_executed(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
        gate = _gate(tmp_path, amount_within_limit, clock=lambda: now)
        verdict = gate.evaluate(
            action="payment.transfer", target="acct:1", parameters={"amount_minor": 1}
        )
        now += timedelta(seconds=3600)
        with pytest.raises(PermitError, match="expired"):
            gate.execute(verdict, lambda: "effect")
