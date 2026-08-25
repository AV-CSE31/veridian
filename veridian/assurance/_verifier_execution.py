"""Verifier provenance and the explicit trusted/isolated execution seam.

This module selects a seam; it does not claim that a timeout or child process is
a sandbox. An ``IsolatedVerifierRunner`` adapter is responsible for enforcing
OS-level network, filesystem, credential, and resource policy.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from veridian.core.exceptions import VeridianError

from ._canonical import (
    decode_profile_v1,
    encode_profile_v1,
    freeze_mapping,
    require_digest,
    require_exact_fields,
    require_string,
    require_string_tuple,
    sha256_digest,
)
from ._decision import ClauseStatus
from ._errors import AssuranceValidationError, AssuranceVerificationError

VERIFIER_MANIFEST_SCHEMA_ID = "veridian.verifier-manifest.v1"
ISOLATED_REQUEST_SCHEMA_ID = "veridian.isolated-verification-request.v1"
ISOLATED_RESULT_SCHEMA_ID = "veridian.isolated-verification-result.v1"


class VerifierExecutionMode(StrEnum):
    TRUSTED_IN_PROCESS = "trusted-in-process"
    ISOLATED = "isolated"


@dataclass(frozen=True)
class VerifierManifestV1:
    """Exact build/configuration identity and declared verifier capabilities."""

    verifier_id: str
    semantic_version: str
    build_digest: str
    config: Mapping[str, object]
    input_schema_digest: str
    output_schema_digest: str
    deterministic: bool
    execution_mode: VerifierExecutionMode
    required_capabilities: tuple[str, ...]
    resource_limits: Mapping[str, object]

    def __post_init__(self) -> None:
        for field_name in ("verifier_id", "semantic_version"):
            object.__setattr__(
                self, field_name, require_string(getattr(self, field_name), field_name)
            )
        for field_name in ("build_digest", "input_schema_digest", "output_schema_digest"):
            object.__setattr__(
                self, field_name, require_digest(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "config", freeze_mapping(self.config, "config"))
        if not isinstance(self.deterministic, bool):
            raise AssuranceValidationError("deterministic must be a boolean")
        if not isinstance(self.execution_mode, VerifierExecutionMode):
            raise AssuranceValidationError("execution_mode must be a VerifierExecutionMode")
        object.__setattr__(
            self,
            "required_capabilities",
            require_string_tuple(self.required_capabilities, "required_capabilities"),
        )
        limits = freeze_mapping(self.resource_limits, "resource_limits")
        for name, value in limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AssuranceValidationError(
                    f"resource limit {name!r} must be a positive integer"
                )
        object.__setattr__(self, "resource_limits", limits)

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": VERIFIER_MANIFEST_SCHEMA_ID,
                "verifier_id": self.verifier_id,
                "semantic_version": self.semantic_version,
                "build_digest": self.build_digest,
                "config": self.config,
                "input_schema_digest": self.input_schema_digest,
                "output_schema_digest": self.output_schema_digest,
                "deterministic": self.deterministic,
                "execution_mode": self.execution_mode.value,
                "required_capabilities": self.required_capabilities,
                "resource_limits": self.resource_limits,
            }
        )

    @property
    def digest(self) -> str:
        return sha256_digest(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> VerifierManifestV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "verifier_id",
                "semantic_version",
                "build_digest",
                "config",
                "input_schema_digest",
                "output_schema_digest",
                "deterministic",
                "execution_mode",
                "required_capabilities",
                "resource_limits",
            }
        )
        require_exact_fields(payload, fields, "VerifierManifestV1")
        if payload["schema_id"] != VERIFIER_MANIFEST_SCHEMA_ID:
            raise AssuranceValidationError("unsupported verifier manifest schema")
        try:
            mode = VerifierExecutionMode(
                require_string(payload["execution_mode"], "execution_mode")
            )
        except ValueError as exc:
            raise AssuranceValidationError("unsupported verifier execution mode") from exc
        config = payload["config"]
        limits = payload["resource_limits"]
        if not isinstance(config, Mapping) or not isinstance(limits, Mapping):
            raise AssuranceValidationError("config and resource_limits must be objects")
        deterministic = payload["deterministic"]
        if not isinstance(deterministic, bool):
            raise AssuranceValidationError("deterministic must be a boolean")
        return cls(
            verifier_id=require_string(payload["verifier_id"], "verifier_id"),
            semantic_version=require_string(payload["semantic_version"], "semantic_version"),
            build_digest=require_digest(payload["build_digest"], "build_digest"),
            config=cast(Mapping[str, object], config),
            input_schema_digest=require_digest(
                payload["input_schema_digest"], "input_schema_digest"
            ),
            output_schema_digest=require_digest(
                payload["output_schema_digest"], "output_schema_digest"
            ),
            deterministic=deterministic,
            execution_mode=mode,
            required_capabilities=require_string_tuple(
                payload["required_capabilities"], "required_capabilities"
            ),
            resource_limits=cast(Mapping[str, object], limits),
        )


@dataclass(frozen=True)
class TrustedVerifierPolicy:
    """Digest allowlist defining the reviewed in-process trusted computing base."""

    allowed_manifest_digests: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_manifest_digests",
            frozenset(
                require_digest(item, "allowed_manifest_digests")
                for item in self.allowed_manifest_digests
            ),
        )


def select_verifier_execution(
    manifest: VerifierManifestV1, policy: TrustedVerifierPolicy
) -> VerifierExecutionMode:
    """Choose in-process only for an exact, deterministic, reviewed manifest."""
    if (
        manifest.execution_mode is VerifierExecutionMode.TRUSTED_IN_PROCESS
        and manifest.deterministic
        and manifest.digest in policy.allowed_manifest_digests
    ):
        return VerifierExecutionMode.TRUSTED_IN_PROCESS
    return VerifierExecutionMode.ISOLATED


@dataclass(frozen=True)
class IsolatedVerificationRequestV1:
    """Narrow exact-byte request passed to an isolation adapter."""

    manifest_bytes: bytes
    snapshot_bytes: bytes
    manifest_digest: str
    snapshot_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_bytes, bytes) or not isinstance(self.snapshot_bytes, bytes):
            raise AssuranceValidationError("isolated request artifacts must be exact bytes")
        manifest = VerifierManifestV1.from_bytes(self.manifest_bytes)
        if require_digest(self.manifest_digest, "manifest_digest") != manifest.digest:
            raise AssuranceValidationError("manifest_digest does not bind manifest_bytes")
        if require_digest(self.snapshot_digest, "snapshot_digest") != sha256_digest(
            self.snapshot_bytes
        ):
            raise AssuranceValidationError("snapshot_digest does not bind snapshot_bytes")

    @classmethod
    def create(
        cls, *, manifest_bytes: bytes, snapshot_bytes: bytes
    ) -> IsolatedVerificationRequestV1:
        manifest = VerifierManifestV1.from_bytes(manifest_bytes)
        return cls(
            manifest_bytes=manifest_bytes,
            snapshot_bytes=snapshot_bytes,
            manifest_digest=manifest.digest,
            snapshot_digest=sha256_digest(snapshot_bytes),
        )

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": ISOLATED_REQUEST_SCHEMA_ID,
                "manifest_bytes_b64": base64.b64encode(self.manifest_bytes).decode("ascii"),
                "snapshot_bytes_b64": base64.b64encode(self.snapshot_bytes).decode("ascii"),
                "manifest_digest": self.manifest_digest,
                "snapshot_digest": self.snapshot_digest,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> IsolatedVerificationRequestV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "manifest_bytes_b64",
                "snapshot_bytes_b64",
                "manifest_digest",
                "snapshot_digest",
            }
        )
        require_exact_fields(payload, fields, "IsolatedVerificationRequestV1")
        if payload["schema_id"] != ISOLATED_REQUEST_SCHEMA_ID:
            raise AssuranceValidationError("unsupported isolated request schema")
        try:
            manifest_bytes = base64.b64decode(
                require_string(payload["manifest_bytes_b64"], "manifest_bytes_b64"), validate=True
            )
            snapshot_bytes = base64.b64decode(
                require_string(payload["snapshot_bytes_b64"], "snapshot_bytes_b64"), validate=True
            )
        except (ValueError, binascii.Error) as exc:
            raise AssuranceValidationError("isolated request contains invalid base64") from exc
        return cls(
            manifest_bytes=manifest_bytes,
            snapshot_bytes=snapshot_bytes,
            manifest_digest=require_digest(payload["manifest_digest"], "manifest_digest"),
            snapshot_digest=require_digest(payload["snapshot_digest"], "snapshot_digest"),
        )


@dataclass(frozen=True)
class IsolatedVerificationResultV1:
    """Exact result whose input bindings are checked by the trusted host."""

    manifest_digest: str
    snapshot_digest: str
    status: ClauseStatus
    reason_code: str
    evidence_ids: tuple[str, ...]
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "manifest_digest", require_digest(self.manifest_digest, "manifest_digest")
        )
        object.__setattr__(
            self, "snapshot_digest", require_digest(self.snapshot_digest, "snapshot_digest")
        )
        if not isinstance(self.status, ClauseStatus):
            raise AssuranceValidationError("status must be a ClauseStatus value")
        object.__setattr__(self, "reason_code", require_string(self.reason_code, "reason_code"))
        object.__setattr__(
            self, "evidence_ids", require_string_tuple(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(self, "details", freeze_mapping(self.details, "details"))

    def to_bytes(self) -> bytes:
        return encode_profile_v1(
            {
                "schema_id": ISOLATED_RESULT_SCHEMA_ID,
                "manifest_digest": self.manifest_digest,
                "snapshot_digest": self.snapshot_digest,
                "status": self.status.value,
                "reason_code": self.reason_code,
                "evidence_ids": self.evidence_ids,
                "details": self.details,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> IsolatedVerificationResultV1:
        payload = decode_profile_v1(data)
        fields = frozenset(
            {
                "schema_id",
                "manifest_digest",
                "snapshot_digest",
                "status",
                "reason_code",
                "evidence_ids",
                "details",
            }
        )
        require_exact_fields(payload, fields, "IsolatedVerificationResultV1")
        if payload["schema_id"] != ISOLATED_RESULT_SCHEMA_ID:
            raise AssuranceValidationError("unsupported isolated result schema")
        try:
            status = ClauseStatus(require_string(payload["status"], "status"))
        except ValueError as exc:
            raise AssuranceValidationError("unsupported isolated verifier status") from exc
        details = payload["details"]
        if not isinstance(details, Mapping):
            raise AssuranceValidationError("isolated result details must be an object")
        return cls(
            manifest_digest=require_digest(payload["manifest_digest"], "manifest_digest"),
            snapshot_digest=require_digest(payload["snapshot_digest"], "snapshot_digest"),
            status=status,
            reason_code=require_string(payload["reason_code"], "reason_code"),
            evidence_ids=require_string_tuple(payload["evidence_ids"], "evidence_ids"),
            details=cast(Mapping[str, object], details),
        )


class IsolatedVerifierRunner(Protocol):
    """Adapter seam for a real OS/container isolation implementation."""

    def evaluate(self, request_bytes: bytes) -> bytes:
        """Evaluate exact bytes in an externally enforced isolation boundary."""
        ...


def run_isolated_verifier(
    runner: IsolatedVerifierRunner, request: IsolatedVerificationRequestV1
) -> IsolatedVerificationResultV1:
    """Invoke an isolation adapter and validate its exact input bindings."""
    try:
        result_bytes = runner.evaluate(request.to_bytes())
    except VeridianError:
        raise
    except Exception as exc:
        raise AssuranceVerificationError(
            f"isolated verifier adapter failed: {type(exc).__name__}"
        ) from exc
    if not isinstance(result_bytes, bytes):
        raise AssuranceVerificationError("isolated verifier adapter must return bytes")
    try:
        result = IsolatedVerificationResultV1.from_bytes(result_bytes)
    except AssuranceValidationError as exc:
        raise AssuranceVerificationError(f"invalid isolated verifier result: {exc}") from exc
    if result.manifest_digest != request.manifest_digest:
        raise AssuranceVerificationError("isolated result has the wrong manifest binding")
    if result.snapshot_digest != request.snapshot_digest:
        raise AssuranceVerificationError("isolated result has the wrong snapshot binding")
    return result
