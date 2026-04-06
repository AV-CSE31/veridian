"""
veridian.observability.proof_chain
───────────────────────────────────
Cryptographic audit chain — hash-linked proof entries for EU AI Act compliance.

Every task execution produces a ProofEntry containing:
  - SHA-256 hashes of task spec, verifier config, inputs, outputs
  - Model version, timestamp, policy attestation
  - Link to previous entry (previous_hash)
  - Optional HMAC signature (chain_signature)

Properties:
  - Retroactive tampering detectable (hash chain breaks)
  - Every decision traceable to actor + model + policy
  - Exportable as JSON/JSONL
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["ProofChain", "ProofEntry"]


@dataclass
class ProofEntry:
    """A single entry in the cryptographic proof chain."""

    task_id: str = ""
    task_spec_hash: str = ""
    verifier_config_hash: str = ""
    model_version: str = ""
    input_hash: str = ""
    output_hash: str = ""
    verification_evidence: dict[str, Any] = field(default_factory=dict)
    policy_attestation: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    previous_hash: str = ""
    chain_signature: str = ""

    def compute_hash(self) -> str:
        """SHA-256 hash of canonical entry fields (excludes chain_signature)."""
        canonical = json.dumps(
            {
                "task_id": self.task_id,
                "task_spec_hash": self.task_spec_hash,
                "verifier_config_hash": self.verifier_config_hash,
                "model_version": self.model_version,
                "input_hash": self.input_hash,
                "output_hash": self.output_hash,
                "verification_evidence": self.verification_evidence,
                "policy_attestation": self.policy_attestation,
                "timestamp": self.timestamp,
                "previous_hash": self.previous_hash,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "task_id": self.task_id,
            "task_spec_hash": self.task_spec_hash,
            "verifier_config_hash": self.verifier_config_hash,
            "model_version": self.model_version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "verification_evidence": self.verification_evidence,
            "policy_attestation": self.policy_attestation,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "chain_signature": self.chain_signature,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProofEntry:
        """Deserialize from dict."""
        return cls(
            task_id=d.get("task_id", ""),
            task_spec_hash=d.get("task_spec_hash", ""),
            verifier_config_hash=d.get("verifier_config_hash", ""),
            model_version=d.get("model_version", ""),
            input_hash=d.get("input_hash", ""),
            output_hash=d.get("output_hash", ""),
            verification_evidence=d.get("verification_evidence", {}),
            policy_attestation=d.get("policy_attestation", []),
            timestamp=d.get("timestamp", ""),
            previous_hash=d.get("previous_hash", ""),
            chain_signature=d.get("chain_signature", ""),
        )


class ProofChain:
    """Hash-linked chain of proof entries with optional HMAC signing."""

    def __init__(self, signing_key: str | None = None) -> None:
        self._entries: list[ProofEntry] = []
        self._signing_key = signing_key

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, entry: ProofEntry) -> None:
        """Append entry, setting previous_hash and optional HMAC signature."""
        if self._entries:
            entry.previous_hash = self._entries[-1].compute_hash()

        if self._signing_key:
            entry.chain_signature = hmac.new(
                self._signing_key.encode(),
                entry.compute_hash().encode(),
                hashlib.sha256,
            ).hexdigest()

        self._entries.append(entry)

    def verify(self) -> bool:
        """Verify chain integrity — check all hash links."""
        if len(self._entries) <= 1:
            return True

        for i in range(1, len(self._entries)):
            expected_prev = self._entries[i - 1].compute_hash()
            if self._entries[i].previous_hash != expected_prev:
                return False
        return True

    def save(self, path: Path) -> None:
        """Atomic write: save chain as JSONL."""
        lines = [json.dumps(e.to_dict()) for e in self._entries]
        content = "\n".join(lines) + "\n" if lines else ""

        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as f:
            f.write(content)
            tmp_path = Path(f.name)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: Path, signing_key: str | None = None) -> ProofChain:
        """Load chain from JSONL file."""
        chain = cls(signing_key=signing_key)
        if not path.exists():
            return chain
        for line in path.read_text().strip().split("\n"):
            line = line.strip()
            if line:
                chain._entries.append(ProofEntry.from_dict(json.loads(line)))
        return chain

    def to_markdown(self) -> str:
        """Generate proof chain markdown summary."""
        lines = [
            "# Cryptographic Proof Chain",
            "",
            f"**Entries:** {len(self._entries)}",
            f"**Chain intact:** {'YES' if self.verify() else 'NO'}",
            "",
        ]
        if self._entries:
            lines.append("| # | Task | Model | Hash (first 12) | Prev (first 12) |")
            lines.append("|---|------|-------|-----------------|-----------------|")
            for i, e in enumerate(self._entries):
                h = e.compute_hash()[:12]
                p = e.previous_hash[:12] if e.previous_hash else "—"
                lines.append(f"| {i + 1} | {e.task_id} | {e.model_version} | {h} | {p} |")
        lines.append("")
        return "\n".join(lines)
