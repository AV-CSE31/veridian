"""
veridian.loop.replay_compat
────────────────────────────
RV3-003: Global replay compatibility envelope.

Generalizes the PRM-only replay snapshot (previously in runner._build_prm_replay_snapshot)
into a runner-level invariant applied to every task. When strict replay mode is
enabled, a mismatch in model_id / prompt_hash / verifier_config between runs
fails the task closed with a deterministic error string — no silent divergence.

Snapshot fields:
- ``model_id``             — provider.model (identity of the LLM)
- ``provider_version``     — optional provider SDK version tag
- ``prompt_hash``          — SHA-256 of task identity + description
- ``verifier_id``          — verifier class id
- ``verifier_config_hash`` — SHA-256 of verifier_config dict (sorted keys)
- ``tool_allowlist_hash``  — SHA-256 of any bash_allowlist metadata (for tool boundary)

Snapshots are stored in ``TaskResult.extras['run_replay_snapshot']`` and
persisted via ``TaskLedger.checkpoint_result``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from veridian.core.task import Task
from veridian.providers.base import LLMProvider

__all__ = [
    "ReplaySnapshot",
    "build_run_replay_snapshot",
    "check_replay_compatibility",
]


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    """Deterministic fingerprint of a task's execution environment."""

    model_id: str
    provider_version: str
    prompt_hash: str
    verifier_id: str
    verifier_config_hash: str
    tool_allowlist_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "prompt_hash": self.prompt_hash,
            "verifier_id": self.verifier_id,
            "verifier_config_hash": self.verifier_config_hash,
            "tool_allowlist_hash": self.tool_allowlist_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> ReplaySnapshot:
        return cls(
            model_id=str(d.get("model_id", "")),
            provider_version=str(d.get("provider_version", "")),
            prompt_hash=str(d.get("prompt_hash", "")),
            verifier_id=str(d.get("verifier_id", "")),
            verifier_config_hash=str(d.get("verifier_config_hash", "")),
            tool_allowlist_hash=str(d.get("tool_allowlist_hash", "")),
        )


def _hash_dict(value: dict[str, Any] | None) -> str:
    """Deterministic SHA-256 of a dict using sorted keys + default serializer."""
    if not value:
        return hashlib.sha256(b"{}").hexdigest()
    serialised = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialised).hexdigest()


def build_run_replay_snapshot(task: Task, provider: LLMProvider) -> ReplaySnapshot:
    """Construct a deterministic ``ReplaySnapshot`` for the given task + provider.

    Every field is derived from data that is stable across runs for the same
    inputs — no timestamps, no random IDs. Two runs with identical task data
    and identical provider configuration MUST produce equal snapshots.
    """
    model_id = str(getattr(provider, "model", "") or type(provider).__name__)
    provider_version = str(getattr(provider, "version", "") or "")

    prompt_material = {
        "task_id": task.id,
        "title": task.title,
        "description": task.description,
    }
    prompt_hash = hashlib.sha256(
        json.dumps(prompt_material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    verifier_id = str(task.verifier_id or "")
    verifier_config_hash = _hash_dict(task.verifier_config)

    tool_allowlist = (
        task.metadata.get("bash_allowlist") if isinstance(task.metadata, dict) else None
    )
    tool_allowlist_hash = _hash_dict(
        {"allowlist": tool_allowlist} if tool_allowlist is not None else None
    )

    return ReplaySnapshot(
        model_id=model_id,
        provider_version=provider_version,
        prompt_hash=prompt_hash,
        verifier_id=verifier_id,
        verifier_config_hash=verifier_config_hash,
        tool_allowlist_hash=tool_allowlist_hash,
    )


def check_replay_compatibility(
    task: Task,
    current: ReplaySnapshot,
    saved: dict[str, str] | None,
    strict: bool,
) -> str | None:
    """Return a deterministic error string on mismatch in strict mode, else None.

    Returns None when:
    - ``saved`` is None (first run — nothing to compare against)
    - ``strict`` is False (loose mode — log but do not fail)
    - all snapshot fields match the saved checkpoint

    Error strings start with ``replay_incompatible:`` and name the first field
    that differs so operators can diff quickly. The message is truncated to
    300 characters to fit verifier error-budget rules.
    """
    if saved is None:
        return None
    if not strict:
        return None

    saved_snapshot = ReplaySnapshot.from_dict(saved)
    if saved_snapshot == current:
        return None

    # Report the first mismatching field deterministically (alphabetic order
    # of snapshot fields keeps error strings stable across runs).
    field_order = (
        "model_id",
        "provider_version",
        "prompt_hash",
        "verifier_id",
        "verifier_config_hash",
        "tool_allowlist_hash",
    )
    for name in field_order:
        if getattr(current, name) != getattr(saved_snapshot, name):
            return (
                f"replay_incompatible: {name} changed between runs "
                f"(task={task.id!r}); strict replay requires blocking."
            )[:300]
    # Shouldn't reach here since snapshots differ, but belt-and-suspenders:
    return f"replay_incompatible: snapshot changed (task={task.id!r})"[:300]
