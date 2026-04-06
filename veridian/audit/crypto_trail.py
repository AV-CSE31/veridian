"""
veridian.audit.crypto_trail
────────────────────────────
SHA-256 hash-chained, tamper-evident audit trail.

Design:
- Each AuditEntry includes a SHA-256 hash of its own content PLUS the
  previous entry's hash (blockchain-style). Retroactive tampering breaks
  every subsequent entry's chain_hash.
- AuditChain is the ordered container; CryptoAuditTrail is the high-level API.
- All file persistence uses atomic writes (os.replace) per CLAUDE.md §1.3.
- Pydantic BaseModel for AuditEntry and AuditChain (frozen=True for entries).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from veridian.core.exceptions import AuditIntegrityError

__all__ = [
    "GENESIS_HASH",
    "AuditEntry",
    "AuditChain",
    "CryptoAuditTrail",
    "make_audit_entry",
]

# Sentinel hash for the very first entry (no predecessor)
GENESIS_HASH = "0" * 64


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _hash_data(data: dict[str, Any]) -> str:
    """SHA-256 of deterministic (sorted-key) JSON encoding of data."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_entry_content(
    entry_id: str,
    task_id: str,
    timestamp_utc: datetime,
    event_type: str,
    data_hash: str,
    previous_hash: str,
) -> str:
    """SHA-256 over the pipe-joined string of all entry identity fields."""
    content = "|".join(
        [
            entry_id,
            task_id,
            timestamp_utc.isoformat(),
            event_type,
            data_hash,
            previous_hash,
        ]
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class AuditEntry(BaseModel):
    """
    A single immutable record in the cryptographic audit chain.

    entry_hash = SHA-256(entry_id | task_id | timestamp | event_type | data_hash | previous_hash)

    Tamper-evident: modifying any field invalidates entry_hash.
    Chain-linked: modifying previous_hash breaks the chain.
    """

    model_config = ConfigDict(frozen=True)

    entry_id: str
    task_id: str
    timestamp_utc: datetime
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    data_hash: str  # SHA-256 of `data`
    previous_hash: str  # hash of previous entry (GENESIS_HASH for first)
    entry_hash: str  # SHA-256 of all identity fields incl. data_hash + previous_hash

    def is_valid(self) -> bool:
        """Return True iff both data_hash and entry_hash are self-consistent."""
        expected_data_hash = _hash_data(self.data)
        if self.data_hash != expected_data_hash:
            return False
        expected_entry_hash = _hash_entry_content(
            self.entry_id,
            self.task_id,
            self.timestamp_utc,
            self.event_type,
            self.data_hash,
            self.previous_hash,
        )
        return self.entry_hash == expected_entry_hash


class AuditChain(BaseModel):
    """
    Ordered sequence of AuditEntry records forming a hash-linked chain.

    Mutable container — entries are appended as events occur.
    """

    model_config = ConfigDict(frozen=False)

    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: list[AuditEntry] = Field(default_factory=list)

    @property
    def tail_hash(self) -> str:
        """Hash of the last appended entry, or GENESIS_HASH for empty chain."""
        if not self.entries:
            return GENESIS_HASH
        return self.entries[-1].entry_hash

    def append(self, entry: AuditEntry) -> None:
        """Append an entry. Caller must set entry.previous_hash = self.tail_hash."""
        self.entries.append(entry)

    def verify_integrity(self) -> bool:
        """
        Verify the entire chain without raising.

        Returns True iff:
        - Every entry's data_hash and entry_hash are self-consistent.
        - Each entry's previous_hash equals the preceding entry's entry_hash.
        - The first entry's previous_hash is GENESIS_HASH.
        """
        prev = GENESIS_HASH
        for entry in self.entries:
            if not entry.is_valid():
                return False
            if entry.previous_hash != prev:
                return False
            prev = entry.entry_hash
        return True

    def to_json(self) -> str:
        """Serialize chain to JSON string."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> AuditChain:
        """Deserialize chain from JSON string."""
        return cls.model_validate_json(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Factory function
# ─────────────────────────────────────────────────────────────────────────────


def make_audit_entry(
    task_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    previous_hash: str = GENESIS_HASH,
) -> AuditEntry:
    """
    Construct a fully-hashed, immutable AuditEntry.

    All hashes are computed at construction; the entry is frozen after creation.
    """
    entry_id = str(uuid.uuid4())
    timestamp_utc = datetime.now(UTC)
    data_hash = _hash_data(data)
    entry_hash = _hash_entry_content(
        entry_id, task_id, timestamp_utc, event_type, data_hash, previous_hash
    )
    return AuditEntry(
        entry_id=entry_id,
        task_id=task_id,
        timestamp_utc=timestamp_utc,
        event_type=event_type,
        data=data,
        data_hash=data_hash,
        previous_hash=previous_hash,
        entry_hash=entry_hash,
    )


# ─────────────────────────────────────────────────────────────────────────────
# High-level API
# ─────────────────────────────────────────────────────────────────────────────


class CryptoAuditTrail:
    """
    High-level API for building and persisting a cryptographic audit trail.

    Usage::

        trail = CryptoAuditTrail()
        trail.append_event("task_001", "verification_result", {"passed": True})
        trail.save(Path("audit_chain.json"))

        loaded = CryptoAuditTrail.load(Path("audit_chain.json"))
        assert loaded.verify_integrity()
    """

    def __init__(self, chain_id: str | None = None) -> None:
        self._chain = AuditChain(chain_id=chain_id) if chain_id else AuditChain()

    @property
    def entry_count(self) -> int:
        return len(self._chain.entries)

    def append_event(
        self,
        task_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> AuditEntry:
        """
        Append a new event to the chain.

        Automatically links to the current tail hash — caller does not need to
        manage previous_hash.
        """
        entry = make_audit_entry(
            task_id=task_id,
            event_type=event_type,
            data=data,
            previous_hash=self._chain.tail_hash,
        )
        self._chain.append(entry)
        return entry

    def verify_integrity(self) -> bool:
        """Return True iff chain is intact (non-raising)."""
        return self._chain.verify_integrity()

    def verify_chain(self, *, strict: bool = True) -> bool:
        """
        Verify chain integrity.

        strict=True  → raises AuditIntegrityError on failure
        strict=False → returns False on failure (no exception)
        """
        ok = self._chain.verify_integrity()
        if not ok and strict:
            raise AuditIntegrityError(
                f"Chain {self._chain.chain_id!r} failed integrity check "
                f"({self.entry_count} entries). One or more entries were tampered with."
            )
        return ok

    def export_json(self) -> str:
        """Export the full chain as a JSON string for external audit tools."""
        return self._chain.to_json()

    def save(self, path: Path) -> None:
        """
        Persist the chain to disk using an atomic write (os.replace).

        Guarantees readers never see a partial write.
        """
        raw = self.export_json()
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
            encoding="utf-8",
        ) as f:
            f.write(raw)
            tmp = Path(f.name)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> CryptoAuditTrail:
        """Load a previously saved chain from disk."""
        raw = path.read_text(encoding="utf-8")
        chain = AuditChain.from_json(raw)
        trail = cls(chain_id=chain.chain_id)
        trail._chain = chain
        return trail
