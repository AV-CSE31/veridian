"""A composed front door over the assurance kernel and the effects boundary.

``Gate`` is porcelain. It owns no trust decisions of its own: every object it
produces is a normal ``veridian.assurance`` / ``veridian.effects`` value that the
offline verifier already understands. What it removes is ceremony — deriving the
digest chain, generating nonces and timestamps, assembling the snapshot, and
wiring the trusted executor — so that the common case is a decorator instead of
seventeen hand-built dataclasses.

The trust boundary is unchanged and deliberate: the decorated function is the
credential holder, it runs only behind a verified single-use permit, and its
result is attested into a signed receipt.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from veridian.assurance import (
    ActionSemanticsV1,
    AuthorizationEnvelope,
    ClauseResultV1,
    DecisionPayloadV1,
    Disposition,
    Ed25519Signer,
    EvidenceRef,
    ProofBundleV1,
    ReceiptStatementV1,
    Signer,
    StaticKeyProvider,
    TransportBinding,
    VerificationKeyProvider,
    VerificationSnapshotV1,
    VerifierManifestV1,
    encode_profile_v1,
    sha256_digest,
    sign_receipt,
)
from veridian.effects import (
    DispatchRequest,
    DispatchResult,
    EffectReceiptType,
    EffectReceiptV1,
    ExecutionOutcome,
    ExecutionPermitV1,
    SqlitePermitStore,
    TrustedExecutor,
    sign_execution_permit,
)

from ._check import Check, CheckContext
from ._errors import GateConfigurationError, GateDeniedError, GateHeldError

CONTRACT_SCHEMA_ID = "veridian.gate.contract.v1"
STATE_SCHEMA_ID = "veridian.gate.state.v1"
GATE_ADAPTER_VERSION = "1"

_T = TypeVar("_T")
Clock = Callable[[], datetime]


def _utc_second(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Verdict:
    """A completed decision, its signed proof, and (on ALLOW) its permit."""

    disposition: Disposition
    semantics: ActionSemanticsV1
    authorization: AuthorizationEnvelope
    decision: DecisionPayloadV1
    proof_bundle: ProofBundleV1
    receipt_envelope: bytes
    signed_permit: bytes | None
    permit: ExecutionPermitV1 | None
    contract_bytes: bytes
    state_digest: str
    policy_digest: str

    @property
    def allowed(self) -> bool:
        return self.disposition is Disposition.ALLOW

    @property
    def clause_results(self) -> tuple[ClauseResultV1, ...]:
        return self.decision.clause_results

    def failed_clauses(self) -> tuple[ClauseResultV1, ...]:
        """Clauses that did not pass, in declaration order."""
        from veridian.assurance import ClauseStatus

        return tuple(
            result
            for result in self.decision.clause_results
            if result.status is not ClauseStatus.SATISFIED
        )

    def reason(self) -> str:
        """A short human-readable summary of why this verdict is what it is."""
        failures = self.failed_clauses()
        if not failures:
            return f"{self.disposition.value}: all {len(self.clause_results)} clauses satisfied"
        detail = ", ".join(f"{item.clause_id}={item.reason_code}" for item in failures)
        return f"{self.disposition.value}: {detail}"


@dataclass(frozen=True)
class GateOutcome:
    """The result of running a guarded function behind an ALLOW permit."""

    verdict: Verdict
    value: Any
    receipt: EffectReceiptV1
    receipt_envelope: bytes
    replayed: bool
    outbox_id: str

    @property
    def allowed(self) -> bool:
        return True

    @property
    def proof_bundle(self) -> ProofBundleV1:
        return self.verdict.proof_bundle


@dataclass
class _FunctionAdapter:
    """Bridges a plain Python callable into the ``EffectAdapter`` protocol.

    The receipt asserts ``ACKNOWLEDGED``: the callable returned without raising.
    That is all an in-process call can honestly witness — it is not evidence that
    a downstream system durably committed anything. An adapter that really can
    observe settlement should assert ``COMMITTED`` itself.
    """

    adapter_id: str
    call: Callable[[], Any]
    clock: Clock
    receipt_type: EffectReceiptType = EffectReceiptType.ACKNOWLEDGED
    result: Any = None
    called: bool = False

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        self.result = self.call()
        self.called = True
        payload = encode_profile_v1(
            {
                "schema_id": "veridian.gate.dispatch-result.v1",
                "repr": repr(self.result)[:4096],
            }
        )
        return DispatchResult(
            producer_id=self.adapter_id,
            receipt_type=self.receipt_type,
            observed_at=_utc_second(self.clock()),
            external_reference_digest=sha256_digest(request.idempotency_key.encode("utf-8")),
            result_digest=sha256_digest(payload),
        )


class Gate:
    """Evaluate an agent's proposed action and execute it behind a signed permit.

    A ``Gate`` binds one audience (the executor that will hold credentials), one
    principal, one purpose and one ordered set of :class:`~veridian.gate.Check`
    instances. Those checks are the contract: their identities are digested into
    ``contract_digest`` and ``policy_digest``, so changing a check changes the
    policy under which every subsequent permit is issued.

    For production, supply an operator-owned ``signer`` (KMS, HSM, or an
    :class:`~veridian.assurance.Ed25519Signer` built from managed key material)
    and a durable ``store_path``. :meth:`for_development` generates ephemeral
    keys and is never appropriate for real authority.
    """

    def __init__(
        self,
        *,
        audience: str,
        principal: str,
        purpose: str,
        checks: Sequence[Check],
        signer: Signer,
        store_path: str | Path,
        permit_keys: VerificationKeyProvider | None = None,
        receipt_signer: Signer | None = None,
        receipt_keys: VerificationKeyProvider | None = None,
        deployment_id: str = "veridian-gate",
        stream_id: str | None = None,
        permit_ttl_seconds: int = 300,
        clock: Clock | None = None,
    ) -> None:
        if not checks:
            raise GateConfigurationError(
                "a Gate requires at least one Check; an empty policy would allow everything"
            )
        seen: set[str] = set()
        for item in checks:
            if not isinstance(item, Check):
                raise GateConfigurationError("checks must contain veridian.gate.Check values")
            if item.clause_id in seen:
                raise GateConfigurationError(f"duplicate clause_id {item.clause_id!r}")
            seen.add(item.clause_id)
        if permit_ttl_seconds <= 0:
            raise GateConfigurationError("permit_ttl_seconds must be positive")
        if permit_keys is None:
            if not isinstance(signer, Ed25519Signer):
                raise GateConfigurationError(
                    "permit_keys is required unless signer is an Ed25519Signer whose "
                    "public key can be derived"
                )
            permit_keys = StaticKeyProvider.from_signers(signer)

        self._audience = audience
        self._principal = principal
        self._purpose = purpose
        self._checks = tuple(checks)
        self._signer = signer
        self._permit_keys = permit_keys
        self._receipt_signer = receipt_signer or signer
        if receipt_keys is None:
            if isinstance(self._receipt_signer, Ed25519Signer):
                receipt_keys = StaticKeyProvider.from_signers(self._receipt_signer)
            else:
                raise GateConfigurationError(
                    "receipt_keys is required unless the receipt signer is an Ed25519Signer"
                )
        self._receipt_keys = receipt_keys
        store = Path(store_path)
        if store.parent != Path(""):
            store.parent.mkdir(parents=True, exist_ok=True)
        self._store = SqlitePermitStore(store)
        self._deployment_id = deployment_id
        self._stream_id = stream_id or f"{audience}:{purpose}"
        self._permit_ttl = timedelta(seconds=permit_ttl_seconds)
        self._clock = clock or _default_clock
        self._sequence = 0
        self._previous_receipt_digest: str | None = None

        self._contract_bytes = self._build_contract_bytes()
        self._contract_digest = sha256_digest(self._contract_bytes)

    @classmethod
    def for_development(
        cls,
        *,
        audience: str,
        checks: Sequence[Check],
        store_path: str | Path,
        principal: str = "agent://development",
        purpose: str = "development",
        clock: Clock | None = None,
        permit_ttl_seconds: int = 300,
    ) -> Gate:
        """Build a Gate with freshly generated ephemeral keys.

        The signing key exists only for the lifetime of this object, so nothing
        it signs can be verified by any other process. Use this for quickstarts,
        tests and local exploration — never to authorize a real effect.
        """
        signer = Ed25519Signer.generate("dev-ephemeral-key")
        return cls(
            audience=audience,
            principal=principal,
            purpose=purpose,
            checks=checks,
            signer=signer,
            store_path=store_path,
            clock=clock,
            permit_ttl_seconds=permit_ttl_seconds,
        )

    @property
    def verification_keys(self) -> VerificationKeyProvider:
        """Public keys needed to verify this gate's permits and receipts."""
        return self._receipt_keys

    @property
    def contract_digest(self) -> str:
        """Digest of the check set that defines this gate's policy."""
        return self._contract_digest

    def _build_contract_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": CONTRACT_SCHEMA_ID,
                "audience": self._audience,
                "purpose": self._purpose,
                "clauses": [
                    {
                        "clause_id": item.clause_id,
                        "severity": item.severity.value,
                        "semantic_version": item.version,
                        "manifest_digest": item.manifest().digest,
                    }
                    for item in self._checks
                ],
            }
        )

    @staticmethod
    def _state_digest(state: Mapping[str, object]) -> str:
        return sha256_digest(
            encode_profile_v1({"schema_id": STATE_SCHEMA_ID, "state": dict(state)})
        )

    def _transport(self, semantics: ActionSemanticsV1, message_id: str) -> TransportBinding:
        return TransportBinding(
            adapter_id="veridian.gate",
            adapter_version=GATE_ADAPTER_VERSION,
            protocol="python-call",
            protocol_version="1",
            message_id=message_id,
            raw_message_digest=sha256_digest(semantics.to_bytes()),
        )

    def evaluate(
        self,
        *,
        action: str,
        target: str,
        parameters: Mapping[str, object],
        state: Mapping[str, object] | None = None,
        evidence: Iterable[EvidenceRef] = (),
    ) -> Verdict:
        """Run every check and produce a signed, offline-verifiable decision.

        Always returns a :class:`Verdict`; denial is a value, not an exception.
        A permit is minted only when the aggregate disposition is ``ALLOW``.
        """
        now = self._clock()
        state = dict(state or {})
        evidence_refs = tuple(evidence)

        semantics = ActionSemanticsV1(action_type=action, target=target, parameters=parameters)
        state_digest = self._state_digest(state)
        policy_digest = self._contract_digest
        nonce = secrets.token_hex(16)

        authorization = AuthorizationEnvelope(
            semantic_kind="action",
            semantic_digest=semantics.digest,
            principal_id=self._principal,
            delegation_chain=(),
            audience=self._audience,
            purpose=self._purpose,
            nonce=nonce,
            not_before=_utc_second(now),
            expires_at=_utc_second(now + self._permit_ttl),
            state_digest=state_digest,
            policy_digest=policy_digest,
        )

        context = CheckContext(
            action_type=action, target=target, parameters=parameters, state=state
        )
        clause_results: list[ClauseResultV1] = []
        manifests: list[VerifierManifestV1] = []
        for item in self._checks:
            result, manifest = item.evaluate(context)
            clause_results.append(result)
            manifests.append(manifest)

        manifest_digests = tuple(dict.fromkeys(manifest.digest for manifest in manifests))
        snapshot = VerificationSnapshotV1(
            authorization_envelope_digest=authorization.digest,
            state_digest=state_digest,
            evidence_ref_digests=tuple(dict.fromkeys(ref.digest for ref in evidence_refs)),
            verifier_manifest_digests=manifest_digests,
            captured_at=_utc_second(now),
        )
        decision = DecisionPayloadV1.decide(
            authorization_envelope_digest=authorization.digest,
            contract_digest=self._contract_digest,
            snapshot_digest=snapshot.digest,
            clause_results=tuple(clause_results),
            policy_digests=(policy_digest,),
            verifier_manifest_digests=manifest_digests,
        )

        transport = self._transport(semantics, message_id=nonce)
        self._sequence += 1
        statement = ReceiptStatementV1(
            decision_digest=decision.digest,
            receipt_id="rcpt_" + decision.digest.removeprefix("sha256:")[:24],
            issued_at=_utc_second(now),
            sequence=self._sequence,
            deployment_id=self._deployment_id,
            transport_binding_digest=transport.digest,
            stream_id=self._stream_id,
            previous_receipt_digest=self._previous_receipt_digest,
        )
        receipt_envelope = sign_receipt(statement, self._receipt_signer)
        self._previous_receipt_digest = sha256_digest(statement.to_bytes())

        bundle = ProofBundleV1(
            semantic_bytes=semantics.to_bytes(),
            authorization_envelope_bytes=authorization.to_bytes(),
            contract_bytes=self._contract_bytes,
            snapshot_bytes=snapshot.to_bytes(),
            transport_binding_bytes=transport.to_bytes(),
            verifier_manifest_bytes=tuple(
                dict.fromkeys(manifest.to_bytes() for manifest in manifests)
            ),
            evidence_ref_bytes=tuple(ref.to_bytes() for ref in evidence_refs),
            decision_bytes=decision.to_bytes(),
            receipt_envelope_bytes=receipt_envelope,
        )

        signed_permit: bytes | None = None
        permit: ExecutionPermitV1 | None = None
        if decision.disposition is Disposition.ALLOW:
            permit = ExecutionPermitV1(
                permit_id="pmt_" + nonce,
                semantic_digest=semantics.digest,
                authorization_envelope_digest=authorization.digest,
                decision_digest=decision.digest,
                contract_digest=self._contract_digest,
                policy_digest=policy_digest,
                state_digest=state_digest,
                principal_id=self._principal,
                audience=self._audience,
                purpose=self._purpose,
                nonce=nonce,
                idempotency_key="idem_" + nonce,
                issued_at=_utc_second(now),
                not_before=_utc_second(now),
                expires_at=_utc_second(now + self._permit_ttl),
                obligations=decision.obligations,
            )
            signed_permit = sign_execution_permit(permit, self._signer)

        return Verdict(
            disposition=decision.disposition,
            semantics=semantics,
            authorization=authorization,
            decision=decision,
            proof_bundle=bundle,
            receipt_envelope=receipt_envelope,
            signed_permit=signed_permit,
            permit=permit,
            contract_bytes=self._contract_bytes,
            state_digest=state_digest,
            policy_digest=policy_digest,
        )

    def execute(
        self,
        verdict: Verdict,
        call: Callable[[], _T],
        *,
        adapter_id: str = "veridian.gate.callable",
        receipt_type: EffectReceiptType = EffectReceiptType.ACKNOWLEDGED,
    ) -> GateOutcome:
        """Run ``call`` exactly once behind ``verdict``'s permit and attest it.

        Re-presenting a verdict whose permit was already redeemed replays the
        stored receipt without invoking ``call`` again — one economic effect per
        permit, durably, across processes sharing the same store.
        """
        if verdict.signed_permit is None:
            raise GateDeniedError(
                f"cannot execute a {verdict.disposition.value} verdict: {verdict.reason()}"
            )
        adapter = _FunctionAdapter(
            adapter_id=adapter_id,
            call=call,
            clock=self._clock,
            receipt_type=receipt_type,
        )
        executor = TrustedExecutor(
            audience=self._audience,
            store=self._store,
            permit_keys=self._permit_keys,
            receipt_keys=self._receipt_keys,
            receipt_signer=self._receipt_signer,
            adapter=adapter,
        )
        outcome: ExecutionOutcome = executor.execute(
            signed_permit=verdict.signed_permit,
            semantics=verdict.semantics,
            current_state_digest=verdict.state_digest,
            current_policy_digest=verdict.policy_digest,
            executed_at=_utc_second(self._clock()),
        )
        return GateOutcome(
            verdict=verdict,
            value=adapter.result if adapter.called else None,
            receipt=outcome.receipt,
            receipt_envelope=outcome.receipt_envelope,
            replayed=outcome.replayed,
            outbox_id=outcome.outbox.outbox_id,
        )

    def guard(
        self,
        action: str,
        *,
        target: Callable[..., str] | str = "default",
        state: Mapping[str, object] | None = None,
        adapter_id: str | None = None,
    ) -> Callable[[Callable[..., _T]], Callable[..., GateOutcome]]:
        """Wrap a credential-holding function so it runs only behind an ALLOW.

        The wrapped function's keyword arguments become the action parameters
        that checks evaluate and that the permit binds. Calling it raises
        :class:`GateDeniedError` on ``DENY`` and :class:`GateHeldError` on
        ``HOLD``; on ``ALLOW`` it returns a :class:`GateOutcome` carrying the
        function's value, the signed receipt and the proof bundle.
        """

        def decorate(function: Callable[..., _T]) -> Callable[..., GateOutcome]:
            @wraps(function)
            def wrapper(**parameters: object) -> GateOutcome:
                resolved_target = target(**parameters) if callable(target) else target
                verdict = self.evaluate(
                    action=action,
                    target=resolved_target,
                    parameters=parameters,
                    state=state,
                )
                if verdict.disposition is Disposition.HOLD:
                    raise GateHeldError(verdict.reason(), verdict=verdict)
                if verdict.disposition is Disposition.DENY:
                    raise GateDeniedError(verdict.reason(), verdict=verdict)
                return self.execute(
                    verdict,
                    lambda: function(**parameters),
                    adapter_id=adapter_id or f"callable:{function.__qualname__}",
                )

            return wrapper

        return decorate
