"""
veridian.hooks.builtin.identity_guard
─────────────────────────────────────
IdentityGuardHook — proactively redacts secrets from all output surfaces.

Complements SecretsGuard verifier:
  - SecretsGuard DETECTS leaks and FAILS verification
  - IdentityGuardHook PREVENTS leaks by redacting proactively

Priority 5: runs early (after LoggingHook at 0, before all others).

Design:
  - before_task(): rotate_check() on secrets provider
  - after_task(): scan + redact all output surfaces
  - Redaction format: [REDACTED:<pattern_or_ref>]
  - Pattern-based detection reuses SecretsGuard regex patterns
  - Value-based detection matches known secret values from provider
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

from veridian.hooks.base import BaseHook
from veridian.integrations.tenancy import TenantIsolationError
from veridian.secrets.base import SecretsProvider

__all__ = ["IdentityGuardHook"]

log = logging.getLogger(__name__)

# ── Secret patterns (shared with SecretsGuard verifier) ──────────────────────

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_api_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}", re.IGNORECASE)),
    (
        "anthropic_api_key",
        re.compile(r"sk-ant-(?:api\d+-)?[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    ),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    ),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_=.+/]{20,}")),
    (
        "db_connection_string",
        re.compile(
            r"(?:postgres(?:ql)?|mysql|mongodb|redis)://[^:]+:[^@\s]+@",
            re.IGNORECASE,
        ),
    ),
    (
        "password_field",
        re.compile(r"""(?i)["']?password["']?\s*[=:]\s*["']?[^\s"',}{>]{4,}"""),
    ),
    (
        "secret_field",
        re.compile(
            r"""(?i)["']?(?:secret|api_key|access_token|auth_token|private_key)"""
            r"""["']?\s*[=:]\s*["']?[^\s"',}{>]{8,}"""
        ),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
]

_REDACT_TEMPLATE = "[REDACTED:{name}]"


class IdentityGuardHook(BaseHook):
    """Proactively redacts secrets from all output surfaces.

    Runs at priority 5 (after logging, before all other hooks).
    Uses pattern-based detection + value-based matching from SecretsProvider.
    """

    id: ClassVar[str] = "identity_guard"
    priority: ClassVar[int] = 5

    def __init__(self, secrets_provider: SecretsProvider) -> None:
        self._secrets_provider = secrets_provider
        self._known_secrets: dict[str, str] = {}  # ref -> value

    def before_run(self, event: Any) -> None:
        """Cache known secret values for value-based redaction."""
        self._known_secrets = {}
        try:
            refs = self._secrets_provider.list_refs()
            for ref in refs:
                try:
                    value = self._secrets_provider.get(ref)
                    if len(value) >= 4:  # skip very short values to avoid false positives
                        self._known_secrets[ref] = value
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            log.debug("identity_guard: could not enumerate secret refs")

    def before_task(self, event: Any) -> None:
        """Call rotate_check() on secrets provider.

        Rotation failures are logged but not propagated — one stale
        credential check should not kill the run.
        """
        tenant_id = getattr(event, "tenant_id", None)
        scope_tenant_id = getattr(event, "scope_tenant_id", None)
        if (
            isinstance(tenant_id, str)
            and isinstance(scope_tenant_id, str)
            and tenant_id != scope_tenant_id
        ):
            raise TenantIsolationError(
                f"Tenant context mismatch: event tenant {tenant_id!r} vs scope {scope_tenant_id!r}"
            )

        try:
            self._secrets_provider.rotate_check()
        except Exception:  # noqa: BLE001
            log.warning("identity_guard: secret rotation check failed")

    def after_task(self, event: Any) -> None:
        """Scan and redact secrets from all output surfaces."""
        result = getattr(event, "result", None)
        if result is None:
            return

        # Redact raw_output
        raw = getattr(result, "raw_output", None)
        if raw and isinstance(raw, str):
            result.raw_output = self._redact(raw)

        # Redact error
        error = getattr(result, "error", None)
        if error and isinstance(error, str):
            result.error = self._redact(error)

        # Redact structured output
        structured = getattr(result, "structured", None)
        if structured and isinstance(structured, dict):
            self._redact_dict(structured)

        # Redact bash outputs
        bash_outputs = getattr(result, "bash_outputs", None)
        if bash_outputs and isinstance(bash_outputs, list):
            for bash in bash_outputs:
                cmd = getattr(bash, "cmd", None)
                if cmd and isinstance(cmd, str):
                    bash.cmd = self._redact(cmd)
                stdout = getattr(bash, "stdout", None)
                if stdout and isinstance(stdout, str):
                    bash.stdout = self._redact(stdout)
                stderr = getattr(bash, "stderr", None)
                if stderr and isinstance(stderr, str):
                    bash.stderr = self._redact(stderr)

    def _redact(self, text: str) -> str:
        """Apply value-based and pattern-based redaction to text."""
        # 1. Value-based: replace known secret values
        for ref, value in self._known_secrets.items():
            if value in text:
                text = text.replace(value, _REDACT_TEMPLATE.format(name=ref))

        # 2. Pattern-based: replace matches from regex patterns
        for name, pattern in _SECRET_PATTERNS:
            text = pattern.sub(_REDACT_TEMPLATE.format(name=name), text)

        return text

    def _redact_dict(self, d: dict[str, Any]) -> None:
        """Recursively redact string values in a dict."""
        for key in d:
            val = d[key]
            if isinstance(val, str):
                d[key] = self._redact(val)
            elif isinstance(val, dict):
                self._redact_dict(val)
            elif isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, str):
                        val[i] = self._redact(item)
                    elif isinstance(item, dict):
                        self._redact_dict(item)
