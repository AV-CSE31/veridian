"""
veridian.audit
──────────────
Cryptographic audit infrastructure for tamper-evident verification logs.
"""

from veridian.audit.crypto_trail import (
    GENESIS_HASH,
    AuditChain,
    AuditEntry,
    CryptoAuditTrail,
    make_audit_entry,
)

__all__ = [
    "GENESIS_HASH",
    "AuditChain",
    "AuditEntry",
    "CryptoAuditTrail",
    "make_audit_entry",
]
