"""
veridian.observability.cross_agent_chain
──────────────────────────────────────────
Cross-agent proof-chain linkage (WCP-012).

When one agent (parent) spawns or delegates to another agent (child),
each produces its own ``ProofChain``. The parent chain records the
delegation; the child chain records the sub-task execution. By
themselves, the two chains are disjoint: an auditor has no
cryptographically sound way to say "this child run is the one the
parent spawned, and it is the only one."

``CrossAgentLink`` closes that gap with a small, pure envelope that
binds a child chain to a parent chain:

    link = build_link(
        parent_chain=parent,
        child_chain=child,
        parent_task_id=parent.tail_task_id,
        child_task_id=child.head_task_id,
    )
    assert verify_link(link, parent_chain=parent, child_chain=child)

The anchor hash is ``SHA-256(parent_tail_hash || child_head_hash ||
parent_task_id || child_task_id)``. Tampering with either chain — or
substituting a different child — breaks the anchor.

Design notes
------------
* **Additive.** No modifications to :class:`ProofEntry` or
  :class:`ProofChain`. The link lives beside them and is serializable
  as JSON alongside a compliance report.
* **One-directional binding.** The link commits the parent's view of
  which child it spawned. A stronger bidirectional binding would also
  require the child's first entry to carry the parent anchor — that is
  a follow-on increment (WCP-012b), not in scope here.
* **Deterministic.** Same inputs, same anchor. Replay safe.
* **No new dependencies.** Uses only ``hashlib`` + ``json``, already
  imported throughout :mod:`veridian.observability`.

Intended use cases
------------------
* Multi-agent workflow traceability — a compliance reviewer can traverse
  parent→child links and produce a single multi-agent transaction graph.
* EU AI Act Annex III accountability — link every sub-agent run to the
  human-overseen supervising run that spawned it.
* Delegation auditing — demonstrate to a third party that the child
  chain they are auditing is in fact the one the parent attested to.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from veridian.observability.proof_chain import ProofChain

__all__ = [
    "CrossAgentLink",
    "CrossAgentLinkError",
    "build_link",
    "verify_link",
]


class CrossAgentLinkError(Exception):
    """Raised when a cross-agent link fails verification."""


@dataclass(frozen=True, slots=True)
class CrossAgentLink:
    """A one-directional binding from a parent ProofChain to a child ProofChain.

    Attributes:
        parent_task_id: Task ID of the parent run that spawned the child.
            Typically the ``task_id`` of the tail ``ProofEntry`` on the
            parent chain, but not required to be — any entry on the
            parent chain is fine.
        child_task_id: Task ID of the child run. Typically the head
            entry's ``task_id``.
        parent_tail_hash: ``compute_hash()`` of the last entry on the
            parent chain **at the moment of linking**. Recording the
            tail hash (not just the linked entry's hash) lets the
            verifier re-derive the anchor and detect any subsequent
            parent-chain tampering.
        child_head_hash: ``compute_hash()`` of the first entry on the
            child chain.
        anchor_hash: ``SHA-256(parent_tail_hash || child_head_hash ||
            parent_task_id || child_task_id)``. Treat as opaque; use
            :func:`verify_link` to check it.
        created_at: ISO-8601 UTC timestamp of link creation. Informational.
        metadata: Optional free-form dict, preserved verbatim. Not
            covered by the anchor hash.
    """

    parent_task_id: str
    child_task_id: str
    parent_tail_hash: str
    child_head_hash: str
    anchor_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_task_id": self.parent_task_id,
            "child_task_id": self.child_task_id,
            "parent_tail_hash": self.parent_tail_hash,
            "child_head_hash": self.child_head_hash,
            "anchor_hash": self.anchor_hash,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrossAgentLink:
        return cls(
            parent_task_id=d["parent_task_id"],
            child_task_id=d["child_task_id"],
            parent_tail_hash=d["parent_tail_hash"],
            child_head_hash=d["child_head_hash"],
            anchor_hash=d["anchor_hash"],
            created_at=d.get("created_at", ""),
            metadata=dict(d.get("metadata", {})),
        )


def _compute_anchor(
    parent_tail_hash: str,
    child_head_hash: str,
    parent_task_id: str,
    child_task_id: str,
) -> str:
    """SHA-256 over a canonical JSON payload (stable field order)."""
    payload = json.dumps(
        {
            "parent_task_id": parent_task_id,
            "child_task_id": child_task_id,
            "parent_tail_hash": parent_tail_hash,
            "child_head_hash": child_head_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _tail_hash(chain: ProofChain) -> str:
    entries = list(chain._entries)  # noqa: SLF001 — intentional read-only peek
    if not entries:
        raise CrossAgentLinkError("cross-agent link requires a non-empty parent ProofChain")
    return entries[-1].compute_hash()


def _head_hash(chain: ProofChain) -> str:
    entries = list(chain._entries)  # noqa: SLF001 — intentional read-only peek
    if not entries:
        raise CrossAgentLinkError("cross-agent link requires a non-empty child ProofChain")
    return entries[0].compute_hash()


def build_link(
    parent_chain: ProofChain,
    child_chain: ProofChain,
    parent_task_id: str,
    child_task_id: str,
    metadata: dict[str, Any] | None = None,
) -> CrossAgentLink:
    """Construct a cross-agent link envelope.

    Args:
        parent_chain: The parent ProofChain. Must contain ≥ 1 entry.
        child_chain: The child ProofChain. Must contain ≥ 1 entry.
        parent_task_id: Task ID of the parent that spawned the child.
        child_task_id: Task ID of the child run.
        metadata: Optional free-form metadata (not anchor-bound).

    Returns:
        A new :class:`CrossAgentLink` with anchor computed.

    Raises:
        CrossAgentLinkError: If either chain is empty.
    """
    parent_tail = _tail_hash(parent_chain)
    child_head = _head_hash(child_chain)
    anchor = _compute_anchor(parent_tail, child_head, parent_task_id, child_task_id)
    return CrossAgentLink(
        parent_task_id=parent_task_id,
        child_task_id=child_task_id,
        parent_tail_hash=parent_tail,
        child_head_hash=child_head,
        anchor_hash=anchor,
        metadata=metadata or {},
    )


def verify_link(
    link: CrossAgentLink,
    parent_chain: ProofChain,
    child_chain: ProofChain,
) -> bool:
    """Verify that a cross-agent link still binds the two chains.

    The check re-derives the anchor from the chains' current tail/head
    hashes and the link's recorded task IDs, then compares against the
    recorded ``anchor_hash``. A tampered parent entry, a substituted
    child chain, or a shuffled head/tail will all cause this to return
    ``False``.

    Note: this does **not** validate internal chain integrity; call
    :meth:`ProofChain.verify` separately if that matters for the audit.

    Args:
        link: The link envelope to verify.
        parent_chain: The parent chain as it exists now.
        child_chain: The child chain as it exists now.

    Returns:
        ``True`` iff the anchor still matches; ``False`` otherwise.
    """
    try:
        current_tail = _tail_hash(parent_chain)
        current_head = _head_hash(child_chain)
    except CrossAgentLinkError:
        return False

    if current_tail != link.parent_tail_hash:
        return False
    if current_head != link.child_head_hash:
        return False

    recomputed = _compute_anchor(
        link.parent_tail_hash,
        link.child_head_hash,
        link.parent_task_id,
        link.child_task_id,
    )
    return recomputed == link.anchor_hash
