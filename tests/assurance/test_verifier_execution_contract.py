from __future__ import annotations

from dataclasses import replace

import pytest

from veridian.assurance import (
    AssuranceVerificationError,
    ClauseStatus,
    IsolatedVerificationRequestV1,
    IsolatedVerificationResultV1,
    TrustedVerifierPolicy,
    VerifierExecutionMode,
    VerifierManifestV1,
    run_isolated_verifier,
    select_verifier_execution,
)


def _manifest(
    mode: VerifierExecutionMode = VerifierExecutionMode.TRUSTED_IN_PROCESS,
) -> VerifierManifestV1:
    return VerifierManifestV1(
        verifier_id="bank.double-entry",
        semantic_version="1.3.0",
        build_digest="sha256:" + "a" * 64,
        config={"currency": "USD", "rounding": "exact-minor-units"},
        input_schema_digest="sha256:" + "b" * 64,
        output_schema_digest="sha256:" + "c" * 64,
        deterministic=True,
        execution_mode=mode,
        required_capabilities=(),
        resource_limits={"cpu_ms": 200, "memory_bytes": 33_554_432},
    )


def test_only_exact_allowlisted_manifest_enters_the_in_process_tcb() -> None:
    manifest = _manifest()
    policy = TrustedVerifierPolicy(frozenset({manifest.digest}))

    assert select_verifier_execution(manifest, policy) is VerifierExecutionMode.TRUSTED_IN_PROCESS

    changed_config = replace(manifest, config={"currency": "EUR"})
    assert changed_config.digest != manifest.digest
    assert select_verifier_execution(changed_config, policy) is VerifierExecutionMode.ISOLATED


def test_non_deterministic_or_explicitly_isolated_verifier_never_enters_tcb() -> None:
    isolated = _manifest(VerifierExecutionMode.ISOLATED)
    non_deterministic = replace(_manifest(), deterministic=False)
    policy = TrustedVerifierPolicy(frozenset({isolated.digest, non_deterministic.digest}))

    assert select_verifier_execution(isolated, policy) is VerifierExecutionMode.ISOLATED
    assert select_verifier_execution(non_deterministic, policy) is VerifierExecutionMode.ISOLATED


def test_untrusted_in_process_request_can_be_safely_routed_to_isolation() -> None:
    manifest = _manifest(VerifierExecutionMode.TRUSTED_IN_PROCESS)
    policy = TrustedVerifierPolicy(frozenset())

    assert select_verifier_execution(manifest, policy) is VerifierExecutionMode.ISOLATED
    request = IsolatedVerificationRequestV1.create(
        manifest_bytes=manifest.to_bytes(),
        snapshot_bytes=b'{"schema_id":"bank.snapshot.v1"}',
    )

    assert request.manifest_digest == manifest.digest


class BoundIsolatedRunner:
    """Public-seam test adapter; it only receives and returns exact bytes."""

    def __init__(self, *, forge_snapshot: bool = False) -> None:
        self.forge_snapshot = forge_snapshot

    def evaluate(self, request_bytes: bytes) -> bytes:
        request = IsolatedVerificationRequestV1.from_bytes(request_bytes)
        return IsolatedVerificationResultV1(
            manifest_digest=request.manifest_digest,
            snapshot_digest=("sha256:" + "f" * 64)
            if self.forge_snapshot
            else request.snapshot_digest,
            status=ClauseStatus.SATISFIED,
            reason_code="DOUBLE_ENTRY_BALANCED",
            evidence_ids=("ev_0123456789abcdef",),
            details={"debits_minor": 125_000, "credits_minor": 125_000},
        ).to_bytes()


def test_isolated_seam_binds_result_to_exact_manifest_and_snapshot() -> None:
    manifest = _manifest(VerifierExecutionMode.ISOLATED)
    request = IsolatedVerificationRequestV1.create(
        manifest_bytes=manifest.to_bytes(),
        snapshot_bytes=b'{"schema_id":"bank.snapshot.v1","state":"held"}',
    )

    result = run_isolated_verifier(BoundIsolatedRunner(), request)

    assert result.manifest_digest == manifest.digest
    assert result.snapshot_digest == request.snapshot_digest


def test_isolated_seam_rejects_a_result_forged_for_another_snapshot() -> None:
    manifest = _manifest(VerifierExecutionMode.ISOLATED)
    request = IsolatedVerificationRequestV1.create(
        manifest_bytes=manifest.to_bytes(),
        snapshot_bytes=b'{"schema_id":"bank.snapshot.v1","state":"held"}',
    )

    with pytest.raises(AssuranceVerificationError, match="snapshot binding"):
        run_isolated_verifier(BoundIsolatedRunner(forge_snapshot=True), request)
