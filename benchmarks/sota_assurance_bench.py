#!/usr/bin/env python3
"""Versioned adversarial and durability benchmark for Veridian assurance surfaces.

The default ``smoke`` profile is intentionally bounded for CI.  An explicit
campaign can be scheduled with, for example::

    python benchmarks/sota_assurance_bench.py --profile campaign --iterations 100000

Observed failures are useful evidence about the exercised schedules.  No
number of passing sampled schedules establishes zero residual risk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from veridian.adapters import (
    ActionSpecV1,
    GenericActionAdapter,
    LangGraphToolCallAdapter,
    MCPToolCallAdapter,
    OpenAIResponsesAdapter,
)
from veridian.assurance import (
    ActionSemanticsV1,
    AssuranceValidationError,
    AuthorizationEnvelope,
    ClauseResultV1,
    ClauseSeverity,
    ClauseStatus,
    DecisionPayloadV1,
    Ed25519Signer,
    StaticKeyProvider,
)
from veridian.effects import (
    ExecutionPermitV1,
    PermitError,
    PermitReplayError,
    SqlitePermitStore,
    sign_execution_permit,
    verify_execution_permit,
)
from veridian.math import (
    AtMostOnceRule,
    BoundInvariant,
    ConservationInvariant,
    ControlLevel,
    ControlPerturbation,
    DeltaComponent,
    InvariantVerifier,
    LinearExpression,
    MathStatus,
    MetamorphicRelation,
    MetamorphicVerifier,
    PrecedenceRule,
    ReasonCode,
    TerminalOutcomeRule,
    TrajectoryEvent,
    TrajectoryVerifier,
    VectorNorm,
)

SCHEMA_ID = "veridian.assurance-benchmark-report.v1"
HARNESS_VERSION = "1.0.0"
DEFAULT_SEED = 20260819
DEFAULT_SMOKE_ITERATIONS = 32
DEFAULT_CAMPAIGN_ITERATIONS = 100_000
CRASH_EXIT_CODE = 91
EXPECTED_ACTION_DIGEST = "sha256:ea1f897fb86084a58b535e3a6a3c2c2810b778ae277dcb07cada86db0568920f"

_STATE = "sha256:" + "5" * 64
_POLICY = "sha256:" + "9" * 64
_CONTRACT = "sha256:" + "c" * 64
_MANIFEST = "sha256:" + "7" * 64
_SIGNING_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")

# Integer weights make schedule construction stable across Python versions.
_CAMPAIGN_WEIGHTS: Mapping[str, int] = {
    "canonical_mutation": 260,
    "permit_context": 180,
    "sqlite_concurrent_redemption": 20,
    "sqlite_crash_recovery": 2,
    "adapter_semantic_determinism": 198,
    "banking_invariant_oracle": 120,
    "metamorphic_control": 110,
    "trajectory_monitor": 110,
}


@dataclass(frozen=True)
class ScenarioObservation:
    """One oracle-checked scenario result."""

    outcome: str
    counters: Mapping[str, int]


@dataclass(frozen=True)
class ScheduleItem:
    """A reproducible scenario choice and its independent deterministic seed."""

    scenario: str
    seed: int


Scenario = Callable[[int, int], ScenarioObservation]


def _stable_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_stable_bytes(value)).hexdigest()


def _seed_material(seed: int, index: int) -> bytes:
    return hashlib.sha256(f"veridian-benchmark-schedule-v1\n{seed}\n{index}".encode()).digest()


def _weighted_choice(names: Sequence[str], seed: int, index: int) -> str:
    weighted = [(name, _CAMPAIGN_WEIGHTS[name]) for name in names]
    total = sum(weight for _, weight in weighted)
    ticket = int.from_bytes(_seed_material(seed, index)[:8], "big") % total
    cumulative = 0
    for name, weight in weighted:
        cumulative += weight
        if ticket < cumulative:
            return name
    raise AssertionError("weighted schedule selection did not terminate")


def build_schedule(names: Sequence[str], *, iterations: int, seed: int) -> tuple[ScheduleItem, ...]:
    """Build a cross-version deterministic schedule that covers every selected case."""

    if not names:
        raise ValueError("at least one benchmark case must be selected")
    if iterations < len(names):
        raise ValueError("iterations must be at least the number of selected benchmark cases")
    items: list[ScheduleItem] = []
    for index in range(iterations):
        name = names[index] if index < len(names) else _weighted_choice(names, seed, index)
        case_seed = int.from_bytes(_seed_material(seed, index)[8:16], "big")
        items.append(ScheduleItem(name, case_seed))
    return tuple(items)


def _action() -> ActionSemanticsV1:
    return ActionSemanticsV1(
        "bank.transfer",
        "account:merchant-42",
        {
            "amount_minor": 125_000,
            "currency": "USD",
            "destination_account": "account:merchant-42",
        },
    )


def _permit_fixture() -> tuple[ActionSemanticsV1, ExecutionPermitV1, Ed25519Signer]:
    action = _action()
    authorization = AuthorizationEnvelope(
        semantic_kind="action",
        semantic_digest=action.digest,
        principal_id="agent:treasury-7",
        delegation_chain=("human:alice", "service:treasury"),
        audience="bank-executor:prod",
        purpose="invoice:INV-314",
        nonce="authorization-0123456789abcdef",
        not_before="2026-08-19T10:00:00Z",
        expires_at="2026-08-19T10:05:00Z",
        state_digest=_STATE,
        policy_digest=_POLICY,
    )
    clause = ClauseResultV1(
        clause_id="bank-controls",
        severity=ClauseSeverity.HARD,
        status=ClauseStatus.SATISFIED,
        reason_code="BANK_CONTROLS_SATISFIED",
        verifier_manifest_digest=_MANIFEST,
        evidence_ids=("ev_0123456789abcdef",),
        details={},
    )
    decision = DecisionPayloadV1.decide(
        authorization_envelope_digest=authorization.digest,
        contract_digest=_CONTRACT,
        snapshot_digest=_STATE,
        clause_results=(clause,),
        policy_digests=(_POLICY,),
        verifier_manifest_digests=(_MANIFEST,),
    )
    permit = ExecutionPermitV1.issue(
        authorization=authorization,
        decision=decision,
        permit_id="permit_0123456789abcdef",
        nonce="permit-nonce-0123456789abcdef",
        idempotency_key="payment-PAY-9001",
        issued_at="2026-08-19T10:00:01Z",
        not_before="2026-08-19T10:00:01Z",
        expires_at="2026-08-19T10:02:00Z",
    )
    signer = Ed25519Signer.from_private_bytes("benchmark-permit-key", _SIGNING_SEED)
    return action, permit, signer


def _redeem(
    store: SqlitePermitStore,
    permit: ExecutionPermitV1,
    *,
    payload: bytes = b'{"amount_minor":125000,"currency":"USD"}',
) -> str:
    return store.redeem(
        permit,
        audience="bank-executor:prod",
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        dispatch_payload=payload,
        redeemed_at="2026-08-19T10:00:10Z",
    ).outbox_id


def _expect_rejection(exception_type: type[BaseException], operation: Callable[[], object]) -> None:
    try:
        operation()
    except exception_type:
        return
    raise AssertionError(f"expected {exception_type.__name__} was not raised")


def _canonical_mutation(_seed: int, _concurrency: int) -> ScenarioObservation:
    action = _action()
    if action.digest != EXPECTED_ACTION_DIGEST:
        raise AssertionError("canonical action digest diverged from the independent golden value")
    if ActionSemanticsV1.from_bytes(action.to_bytes()) != action:
        raise AssertionError("canonical action did not round-trip")

    encoded = action.to_bytes()
    decoded = json.loads(encoded)
    reordered = json.dumps(
        dict(reversed(tuple(decoded.items()))),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    mutations = (
        b" " + encoded,
        encoded.replace(b"125000", b"125000.0"),
        encoded[:-1] + b',"target":"account:attacker"}',
        encoded.replace(b'"USD"', '"e\u0301"'.encode()),
        encoded + b"\n",
        reordered,
    )
    for mutation in mutations:
        _expect_rejection(
            AssuranceValidationError,
            lambda mutation=mutation: ActionSemanticsV1.from_bytes(mutation),
        )

    changed = replace(
        action,
        parameters={**action.parameters, "amount_minor": 125_001},
    )
    if changed.digest == action.digest:
        raise AssertionError("business-value mutation did not change semantic identity")
    return ScenarioObservation(
        "all_mutations_bound_or_rejected",
        {"canonical_acceptances": 1, "mutation_rejections": 6, "digest_changes": 1},
    )


def _permit_context(_seed: int, _concurrency: int) -> ScenarioObservation:
    action, permit, signer = _permit_fixture()
    envelope = sign_execution_permit(permit, signer)
    keys = StaticKeyProvider.from_signers(signer)

    verified = verify_execution_permit(
        envelope,
        keys=keys,
        semantics=action,
        expected_audience="bank-executor:prod",
        current_state_digest=_STATE,
        current_policy_digest=_POLICY,
        verified_at="2026-08-19T10:00:10Z",
    )
    if verified.permit.digest != permit.digest:
        raise AssertionError("verified signed permit payload changed")

    changed_action = replace(
        action,
        parameters={**action.parameters, "amount_minor": 125_001},
    )
    cases = (
        lambda: verify_execution_permit(
            envelope,
            keys=keys,
            semantics=changed_action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        ),
        lambda: verify_execution_permit(
            envelope,
            keys=keys,
            semantics=action,
            expected_audience="bank-executor:staging",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        ),
        lambda: verify_execution_permit(
            envelope,
            keys=keys,
            semantics=action,
            expected_audience="bank-executor:prod",
            current_state_digest="sha256:" + "0" * 64,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        ),
        lambda: verify_execution_permit(
            envelope,
            keys=keys,
            semantics=action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest="sha256:" + "0" * 64,
            verified_at="2026-08-19T10:00:10Z",
        ),
        lambda: verify_execution_permit(
            envelope,
            keys=keys,
            semantics=action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:03:00Z",
        ),
        lambda: verify_execution_permit(
            envelope[:-1] + bytes([envelope[-1] ^ 1]),
            keys=keys,
            semantics=action,
            expected_audience="bank-executor:prod",
            current_state_digest=_STATE,
            current_policy_digest=_POLICY,
            verified_at="2026-08-19T10:00:10Z",
        ),
    )
    for operation in cases:
        _expect_rejection(PermitError, operation)
    return ScenarioObservation(
        "signed_context_checks_fail_closed",
        {"valid_verifications": 1, "context_rejections": 5, "tamper_rejections": 1},
    )


def _sqlite_concurrent_redemption(_seed: int, concurrency: int) -> ScenarioObservation:
    _, permit, _ = _permit_fixture()
    attempts = max(2, concurrency * 2)
    with tempfile.TemporaryDirectory(prefix="veridian-permit-race-") as directory:
        path = Path(directory) / "effects.db"
        store = SqlitePermitStore(path)
        store.register(permit)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outbox_ids = tuple(pool.map(lambda _: _redeem(store, permit), range(attempts)))
        if len(set(outbox_ids)) != 1:
            raise AssertionError("exact concurrent retries produced multiple outbox identities")
        if store.redemption_count(permit.permit_id) != 1:
            raise AssertionError("permit redemption count was not exactly one")
        if len(SqlitePermitStore(path).pending_outbox()) != 1:
            raise AssertionError("durable outbox did not recover exactly one dispatch intent")
        _expect_rejection(
            PermitReplayError,
            lambda: _redeem(
                store,
                permit,
                payload=b'{"amount_minor":125001,"currency":"USD"}',
            ),
        )
    return ScenarioObservation(
        "one_redemption_one_outbox",
        {
            "concurrent_attempts": attempts,
            "redemptions": 1,
            "durable_outbox_records": 1,
            "changed_replay_rejections": 1,
        },
    )


def _run_crash_worker(database: Path, stage: str) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-crash",
        "--worker-database",
        str(database),
        "--worker-stage",
        stage,
    ]
    options: dict[str, Any] = {
        "cwd": str(Path(__file__).resolve().parents[1]),
        "capture_output": True,
        "timeout": 30,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    completed = subprocess.run(command, **options)
    if completed.returncode != CRASH_EXIT_CODE:
        stderr = completed.stderr.decode(errors="replace") if completed.stderr else ""
        raise AssertionError(
            f"crash worker exited {completed.returncode}, expected {CRASH_EXIT_CODE}: {stderr}"
        )


def _sqlite_crash_recovery(_seed: int, _concurrency: int) -> ScenarioObservation:
    _, permit, _ = _permit_fixture()
    with tempfile.TemporaryDirectory(prefix="veridian-permit-crash-") as directory:
        root = Path(directory)

        registered_path = root / "registered.db"
        _run_crash_worker(registered_path, "after_register")
        registered = SqlitePermitStore(registered_path)
        if registered.redemption_count(permit.permit_id) != 0:
            raise AssertionError("registration-only crash consumed the permit")
        if registered.pending_outbox():
            raise AssertionError("registration-only crash created an outbox record")
        _redeem(registered, permit)
        if len(SqlitePermitStore(registered_path).pending_outbox()) != 1:
            raise AssertionError("recovered registered permit could not be consumed")

        redeemed_path = root / "redeemed.db"
        _run_crash_worker(redeemed_path, "after_redeem")
        redeemed = SqlitePermitStore(redeemed_path)
        if redeemed.redemption_count(permit.permit_id) != 1:
            raise AssertionError("committed redemption was not recovered after abrupt exit")
        if len(redeemed.pending_outbox()) != 1:
            raise AssertionError("pending dispatch intent was not recovered after abrupt exit")

    return ScenarioObservation(
        "committed_boundaries_recovered",
        {
            "abrupt_process_exits": 2,
            "registered_permits_recovered": 1,
            "redeemed_permits_recovered": 1,
            "pending_outbox_records_recovered": 1,
        },
    )


def _adapter_semantic_determinism(_seed: int, _concurrency: int) -> ScenarioObservation:
    specs = {"transfer_funds": ActionSpecV1("bank.transfer", "destination_account")}
    arguments = {
        "amount_minor": 125_000,
        "currency": "USD",
        "destination_account": "account:merchant-42",
    }
    normalized = (
        OpenAIResponsesAdapter(specs).normalize(
            {
                "type": "function_call",
                "call_id": "call-openai",
                "name": "transfer_funds",
                "arguments": (
                    '{"amount_minor":125000,"currency":"USD",'
                    '"destination_account":"account:merchant-42"}'
                ),
            }
        ),
        MCPToolCallAdapter(specs, protocol_version="2025-06-18").normalize(
            {
                "jsonrpc": "2.0",
                "id": "call-mcp",
                "method": "tools/call",
                "params": {"name": "transfer_funds", "arguments": arguments},
            }
        ),
        LangGraphToolCallAdapter(specs).normalize(
            {
                "id": "call-langgraph",
                "name": "transfer_funds",
                "args": arguments,
                "type": "tool_call",
            }
        ),
        GenericActionAdapter(specs).normalize(
            {
                "schema_id": "veridian.generic-action.v1",
                "message_id": "call-generic",
                "action": "transfer_funds",
                "arguments": arguments,
            }
        ),
    )
    semantic_digests = {item.semantics.digest for item in normalized}
    transport_digests = {item.transport.digest for item in normalized}
    if semantic_digests != {EXPECTED_ACTION_DIGEST}:
        raise AssertionError("protocol adapters diverged from the golden semantic identity")
    if len(transport_digests) != 4:
        raise AssertionError("distinct protocol records did not retain distinct provenance")

    generic = GenericActionAdapter(specs)
    base = {
        "schema_id": "veridian.generic-action.v1",
        "message_id": "generic-1",
        "action": "transfer_funds",
        "arguments": arguments,
    }
    original = generic.normalize(base)
    transport_changed = generic.normalize({**base, "message_id": "generic-2"})
    business_changed = generic.normalize(
        {**base, "arguments": {**arguments, "amount_minor": 125_001}}
    )
    if original.semantics.digest != transport_changed.semantics.digest:
        raise AssertionError("transport-only mutation changed business semantics")
    if original.transport.digest == transport_changed.transport.digest:
        raise AssertionError("transport-only mutation was not bound")
    if original.semantics.digest == business_changed.semantics.digest:
        raise AssertionError("business mutation was not bound")
    return ScenarioObservation(
        "cross_protocol_semantics_stable",
        {
            "protocols": 4,
            "shared_semantic_identities": 1,
            "distinct_transport_identities": 4,
            "transport_only_invariances": 1,
            "business_digest_changes": 1,
        },
    )


def _banking_invariant_oracle(seed: int, _concurrency: int) -> ScenarioObservation:
    transfer = 25_000_000 + seed % 10_000
    fee = 100 + seed % 900
    verifier = InvariantVerifier(
        (
            ConservationInvariant(
                invariant_id="posting-conservation",
                inflows=("source_debit_minor",),
                outflows=("beneficiary_credit_minor", "fee_minor"),
                unit="USD-cent",
            ),
            BoundInvariant(
                invariant_id="liquidity-floor",
                expression=LinearExpression.field("post_available_minor", unit="USD-cent"),
                lower=5_000_000,
            ),
        )
    )
    valid = {
        "source_debit_minor": transfer + fee,
        "beneficiary_credit_minor": transfer,
        "fee_minor": fee,
        "post_available_minor": 70_000_000,
    }
    satisfied = verifier.verify(valid)
    violated = verifier.verify({**valid, "beneficiary_credit_minor": transfer - 1})
    if satisfied.status is not MathStatus.SATISFIED:
        raise AssertionError("exact balanced bank posting failed the independent oracle")
    first = violated.results[0]
    if (
        violated.status is not MathStatus.VIOLATED
        or first.reason_code is not ReasonCode.CONSERVATION_VIOLATED
        or first.margin != Decimal(-1)
    ):
        raise AssertionError("one-cent imbalance was not exposed with an exact witness")
    return ScenarioObservation(
        "exact_oracle_agreement",
        {"balanced_postings_accepted": 1, "one_cent_imbalances_detected": 1},
    )


def _metamorphic_control(seed: int, _concurrency: int) -> ScenarioObservation:
    delta = 1 + seed % 25_000_000
    verifier = MetamorphicVerifier(
        clause_id="amount-control-monotonicity",
        relation=MetamorphicRelation.CONTROL_NON_DECREASING,
        norm=VectorNorm.L1,
        radius=25_000_000,
        input_unit="USD-cent",
    )
    passing = verifier.verify(
        ControlLevel.HOLD,
        (
            ControlPerturbation(
                "higher-amount-stronger-control",
                "increase-transfer-amount",
                (DeltaComponent("amount_minor", delta),),
                ControlLevel.DENY,
            ),
        ),
    )
    violating = verifier.verify(
        ControlLevel.HOLD,
        (
            ControlPerturbation(
                "higher-amount-weaker-control",
                "increase-transfer-amount",
                (DeltaComponent("amount_minor", delta),),
                ControlLevel.ALLOW,
            ),
        ),
    )
    if passing.status is not MathStatus.SATISFIED:
        raise AssertionError("stronger control failed monotonicity relation")
    if violating.status is not MathStatus.VIOLATED or violating.counterexample is None:
        raise AssertionError("weaker control did not produce a metamorphic counterexample")
    return ScenarioObservation(
        "metamorphic_oracle_agreement",
        {"monotone_transformations_accepted": 1, "weakening_transformations_detected": 1},
    )


def _trajectory_event(sequence: int, event_type: str, occurred_at_ms: int) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=f"payment-9001:{sequence}:{event_type}",
        subject_id="payment-intent:sha256:4ad9",
        event_type=event_type,
        sequence=sequence,
        occurred_at_ms=occurred_at_ms,
        evidence_id=f"signed-event:{sequence}:{event_type}",
    )


def _trajectory_monitor(seed: int, _concurrency: int) -> ScenarioObservation:
    delay = seed % 1_000
    verifier = TrajectoryVerifier(
        (
            PrecedenceRule("authorization-before-dispatch", "authorized", "dispatched"),
            AtMostOnceRule("single-use-permit", "permit_redeemed"),
            TerminalOutcomeRule(
                "authorized-eventually-terminal",
                start_event="authorized",
                terminal_events=frozenset({"settled", "failed", "compensated"}),
                max_delay_ms=600_000,
            ),
        )
    )
    valid = verifier.verify(
        (
            _trajectory_event(1, "authorized", 1_000),
            _trajectory_event(2, "permit_redeemed", 2_000),
            _trajectory_event(3, "dispatched", 3_000),
            _trajectory_event(4, "settled", 4_000 + delay),
        ),
        complete=True,
    )
    replay = verifier.verify(
        (
            _trajectory_event(1, "authorized", 1_000),
            _trajectory_event(2, "permit_redeemed", 2_000),
            _trajectory_event(3, "permit_redeemed", 2_001),
            _trajectory_event(4, "dispatched", 3_000),
            _trajectory_event(5, "settled", 4_000),
        ),
        complete=True,
    )
    incomplete = verifier.verify(
        (_trajectory_event(1, "authorized", 1_000),),
        complete=False,
    )
    if valid.status is not MathStatus.SATISFIED:
        raise AssertionError("valid authenticated payment trajectory was rejected")
    if replay.status is not MathStatus.VIOLATED:
        raise AssertionError("permit replay trajectory was not rejected")
    if incomplete.status is not MathStatus.UNKNOWN:
        raise AssertionError("open bounded trajectory did not remain UNKNOWN")
    return ScenarioObservation(
        "trajectory_oracle_agreement",
        {
            "valid_trajectories_accepted": 1,
            "replay_trajectories_detected": 1,
            "open_trajectories_held_unknown": 1,
        },
    )


SCENARIOS: Mapping[str, Scenario] = {
    "canonical_mutation": _canonical_mutation,
    "permit_context": _permit_context,
    "sqlite_concurrent_redemption": _sqlite_concurrent_redemption,
    "sqlite_crash_recovery": _sqlite_crash_recovery,
    "adapter_semantic_determinism": _adapter_semantic_determinism,
    "banking_invariant_oracle": _banking_invariant_oracle,
    "metamorphic_control": _metamorphic_control,
    "trajectory_monitor": _trajectory_monitor,
}


def _percentile(samples: Sequence[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _latency_summary(samples: Sequence[float]) -> Mapping[str, float]:
    return {
        "min": round(min(samples), 6),
        "p50": _percentile(samples, 0.50),
        "p95": _percentile(samples, 0.95),
        "p99": _percentile(samples, 0.99),
        "max": round(max(samples), 6),
    }


def _environment() -> Mapping[str, object]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "logical_cpu_count": os.cpu_count(),
    }


def _schedule_fingerprint(schedule: Sequence[ScheduleItem]) -> str:
    digest = hashlib.sha256()
    digest.update(b"veridian-benchmark-schedule.v1\n")
    for index, item in enumerate(schedule):
        digest.update(f"{index}\t{item.scenario}\t{item.seed}\n".encode())
    return "sha256:" + digest.hexdigest()


def _selected_cases(value: str | None) -> tuple[str, ...]:
    if value is None:
        return tuple(SCENARIOS)
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    unknown = sorted(set(names) - SCENARIOS.keys())
    if unknown:
        raise ValueError(f"unknown benchmark cases: {', '.join(unknown)}")
    if not names:
        raise ValueError("--cases must select at least one benchmark case")
    return names


def run_benchmark(
    *,
    profile: str,
    iterations: int,
    seed: int,
    concurrency: int,
    names: Sequence[str],
    dry_run: bool,
) -> dict[str, object]:
    schedule = build_schedule(names, iterations=iterations, seed=seed)
    distribution = dict(sorted(Counter(item.scenario for item in schedule).items()))
    schedule_fingerprint = _schedule_fingerprint(schedule)
    config = {
        "profile": profile,
        "iterations": iterations,
        "seed": seed,
        "concurrency": concurrency,
        "cases": list(names),
    }
    base: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "harness_version": HARNESS_VERSION,
        "dry_run": dry_run,
        "environment": _environment(),
        "config": config,
        "schedule_distribution": distribution,
        "schedule_fingerprint": schedule_fingerprint,
        "risk_statement": (
            "Passing sampled schedules is not a zero-risk claim; it only reports no observed "
            "failure in the exact versioned cases, inputs, environment, and schedule shown here."
        ),
        "limitations": [
            "The campaign samples a finite schedule and does not prove correctness for all inputs.",
            "Abrupt-exit recovery is not a VM power-cut, storage-controller, or filesystem proof.",
            "Latency is wall-clock diagnostic data and is not reproducible across hardware loads.",
            "Adapter cases cover the protocol shapes implemented by this repository version.",
            "Mathematical cases are model-relative and depend on the disclosed finite oracles.",
        ],
    }
    if dry_run:
        fingerprint_payload = {
            "schema_id": SCHEMA_ID,
            "harness_version": HARNESS_VERSION,
            "config": config,
            "schedule_fingerprint": schedule_fingerprint,
        }
        base.update(
            {
                "passed": None,
                "results": {},
                "totals": {
                    "iterations": iterations,
                    "passes": 0,
                    "failures": 0,
                    "skipped": iterations,
                },
                "reproducibility_fingerprint": _digest(fingerprint_payload),
            }
        )
        return base

    latencies: dict[str, list[float]] = defaultdict(list)
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    observations: dict[str, Counter[str]] = defaultdict(Counter)
    failures: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    outcome_trace: list[Mapping[str, object]] = []
    passes = 0
    for index, item in enumerate(schedule):
        started = time.perf_counter_ns()
        try:
            observation = SCENARIOS[item.scenario](item.seed, concurrency)
        except Exception as exc:  # noqa: BLE001 - failures are benchmark evidence
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            latencies[item.scenario].append(elapsed)
            outcomes[item.scenario]["failed"] += 1
            failure = {
                "iteration": index,
                "seed": item.seed,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            failures[item.scenario].append(failure)
            outcome_trace.append(
                {
                    "scenario": item.scenario,
                    "seed": item.seed,
                    "outcome": "failed",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        latencies[item.scenario].append(elapsed)
        outcomes[item.scenario][observation.outcome] += 1
        observations[item.scenario].update(observation.counters)
        outcome_trace.append(
            {"scenario": item.scenario, "seed": item.seed, "outcome": observation.outcome}
        )
        passes += 1

    result_report: dict[str, object] = {}
    for name in names:
        attempts = distribution.get(name, 0)
        result_report[name] = {
            "attempts": attempts,
            "passes": attempts - len(failures[name]),
            "failures": len(failures[name]),
            "outcome_distribution": dict(sorted(outcomes[name].items())),
            "observation_counts": dict(sorted(observations[name].items())),
            "latency_ms": _latency_summary(latencies[name]),
            "failure_samples": failures[name][:10],
        }

    failure_count = iterations - passes
    totals = {
        "iterations": iterations,
        "passes": passes,
        "failures": failure_count,
        "skipped": 0,
    }
    fingerprint_payload = {
        "schema_id": SCHEMA_ID,
        "harness_version": HARNESS_VERSION,
        "config": config,
        "schedule_fingerprint": schedule_fingerprint,
        "outcomes": outcome_trace,
    }
    base.update(
        {
            "passed": failure_count == 0,
            "results": result_report,
            "totals": totals,
            "reproducibility_fingerprint": _digest(fingerprint_payload),
        }
    )
    return base


def _crash_worker(database: str, stage: str) -> None:
    _, permit, _ = _permit_fixture()
    store = SqlitePermitStore(database)
    store.register(permit)
    if stage == "after_redeem":
        _redeem(store, permit)
    os._exit(CRASH_EXIT_CODE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "campaign"), default="smoke")
    parser.add_argument(
        "--iterations",
        type=int,
        help="number of deterministic scenario schedules (campaign example: 100000)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--cases",
        help="comma-separated case names; defaults to every versioned case",
    )
    parser.add_argument("--dry-run", action="store_true", help="build/report schedule only")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--output", type=Path, help="also write the JSON report to this path")
    parser.add_argument("--indent", type=int, default=2)
    parser.add_argument("--worker-crash", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-database", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-stage",
        choices=("after_register", "after_redeem"),
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.worker_crash:
        if arguments.worker_database is None or arguments.worker_stage is None:
            parser.error("crash worker requires database and stage")
        _crash_worker(arguments.worker_database, arguments.worker_stage)
        return CRASH_EXIT_CODE
    if arguments.list_cases:
        print("\n".join(SCENARIOS))
        return 0
    iterations = arguments.iterations
    if iterations is None:
        iterations = (
            DEFAULT_SMOKE_ITERATIONS
            if arguments.profile == "smoke"
            else DEFAULT_CAMPAIGN_ITERATIONS
        )
    if iterations <= 0:
        parser.error("--iterations must be positive")
    if arguments.concurrency <= 0:
        parser.error("--concurrency must be positive")
    try:
        names = _selected_cases(arguments.cases)
        report = run_benchmark(
            profile=arguments.profile,
            iterations=iterations,
            seed=arguments.seed,
            concurrency=arguments.concurrency,
            names=names,
            dry_run=arguments.dry_run,
        )
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=arguments.indent, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
