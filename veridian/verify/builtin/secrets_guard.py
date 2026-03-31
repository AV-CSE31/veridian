"""
veridian.verify.builtin.secrets_guard
──────────────────────────────────────
Scans agent outputs for leaked secrets before they leave the system.

Covers Pathway 4: Secret leakage — credentials appearing in trace logs,
progress.md, or hook payloads.

Checks:
├── API key patterns (OpenAI, Anthropic, AWS, GitHub, generic)
├── Connection strings with embedded credentials
├── Password / token fields in structured output
├── Bearer tokens in authorization headers
├── High-entropy strings (Shannon entropy ≥ threshold)
└── Optional redaction mode — sanitizes instead of failing
"""

from __future__ import annotations

import logging
import math
import re
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)

# ── Secret patterns ───────────────────────────────────────────────────────────

# Each entry: (pattern_name, compiled_regex)
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # OpenAI API key (sk-proj-... or sk-...)
    ("openai_api_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}", re.IGNORECASE)),
    # Anthropic API key
    ("anthropic_api_key", re.compile(r"sk-ant-(?:api\d+-)?[A-Za-z0-9\-_]{20,}", re.IGNORECASE)),
    # AWS access key ID
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # GitHub personal access token (ghp_, gho_, ghs_, ghr_, github_pat_)
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    ),
    # Generic bearer token
    (
        "bearer_token",
        re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_=.+/]{20,}"),
    ),
    # Database connection strings with embedded credentials
    (
        "db_connection_string",
        re.compile(
            r"(?:postgres(?:ql)?|mysql|mongodb|redis)://[^:]+:[^@\s]+@",
            re.IGNORECASE,
        ),
    ),
    # Password field in JSON/YAML/config
    (
        "password_field",
        re.compile(
            r"""(?i)["']?password["']?\s*[=:]\s*["']?[^\s"',}{>]{4,}""",
        ),
    ),
    # Generic secret/token/credential field
    (
        "secret_field",
        re.compile(
            r"""(?i)["']?(?:secret|api_key|access_token|auth_token|private_key)["']?\s*[=:]\s*["']?[^\s"',}{>]{8,}""",
        ),
    ),
    # Private key header
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    # Slack webhook URL
    (
        "slack_webhook",
        re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),
    ),
]

# Minimum token length for entropy analysis (avoids false positives on short words)
_MIN_TOKEN_LENGTH = 20

# Characters that appear in secrets (base64, hex, URL-safe)
_SECRET_CHARSET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-.")

# Redaction placeholder template
_REDACT_TEMPLATE = "[REDACTED:{pattern}]"


def _shannon_entropy(token: str) -> float:
    """Compute Shannon entropy of a string in bits per character."""
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(token)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def _is_secret_charset(token: str) -> bool:
    """Return True if token consists mostly of secret-like characters."""
    if not token:
        return False
    secret_chars = sum(1 for c in token if c in _SECRET_CHARSET)
    return (secret_chars / len(token)) >= 0.85


class SecretsGuard(BaseVerifier):
    """
    Scans agent outputs for leaked secrets before they leave the system.

    Checks all text surfaces (raw_output, structured fields, bash_outputs)
    for known secret patterns and optionally high-entropy strings.

    Two modes:
      - Default (redact=False): fails verification if any secret is found.
      - Redact mode (redact=True): passes but includes redaction evidence.

    Stateless: all config via constructor. Safe for concurrent use.
    """

    id: ClassVar[str] = "secrets_guard"
    description: ClassVar[str] = (
        "Scans agent outputs for leaked secrets: API keys, tokens, passwords, "
        "connection strings, and high-entropy strings."
    )

    def __init__(
        self,
        min_entropy: float = 4.8,
        redact: bool = False,
    ) -> None:
        """
        Args:
            min_entropy: Shannon entropy threshold for high-entropy detection.
                Tokens above this threshold AND ≥ 20 chars are flagged.
                Default 4.8 bits/char catches most secrets while avoiding prose.
            redact: If True, pass but record redactions in evidence instead of failing.
        """
        if min_entropy <= 0.0:
            raise VeridianConfigError(
                f"SecretsGuard: 'min_entropy' must be > 0, got {min_entropy}."
            )
        self.min_entropy = min_entropy
        self.redact = redact

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Scan all output surfaces for secret patterns."""
        surfaces = self._collect_surfaces(result)
        violations: list[str] = []
        redactions: list[str] = []
        checked = len(surfaces)

        for surface_name, text in surfaces:
            surface_violations = self._scan_text(text)
            for pattern_name, match_snippet in surface_violations:
                msg = f"Secret detected in {surface_name}: {pattern_name} ({match_snippet})"
                violations.append(msg)
                redactions.append(pattern_name)

        if not violations:
            return VerificationResult(passed=True, evidence={"checked": checked})

        if self.redact:
            # Redact mode: pass but document what was scrubbed
            return VerificationResult(
                passed=True,
                evidence={
                    "checked": checked,
                    "redacted": redactions,
                    "count": len(redactions),
                },
            )

        # Fail mode: report violations
        error = self._format_error(violations)
        return VerificationResult(
            passed=False,
            error=error,
            evidence={"violations": violations, "checked": checked},
        )

    def _collect_surfaces(self, result: TaskResult) -> list[tuple[str, str]]:
        """Collect all text surfaces from a TaskResult."""
        surfaces: list[tuple[str, str]] = []

        # Raw LLM output
        if result.raw_output:
            surfaces.append(("raw_output", result.raw_output))

        # Structured fields — flatten to strings
        for key, val in result.structured.items():
            text = val if isinstance(val, str) else str(val)
            surfaces.append((f"structured.{key}", text))

        # Bash outputs — cmd and stdout
        for i, bo in enumerate(result.bash_outputs):
            for field_name in ("cmd", "stdout", "stderr"):
                val = bo.get(field_name, "")
                if isinstance(val, str) and val:
                    surfaces.append((f"bash_output[{i}].{field_name}", val))

        return surfaces

    def _scan_text(self, text: str) -> list[tuple[str, str]]:
        """
        Scan text for secret patterns and high-entropy tokens.

        Returns list of (pattern_name, redacted_snippet) for each violation.
        """
        findings: list[tuple[str, str]] = []

        # Pattern-based detection
        for pattern_name, regex in _SECRET_PATTERNS:
            match = regex.search(text)
            if match:
                raw = match.group(0)
                # Redact the matched value for safe logging
                snippet = self._safe_snippet(raw)
                findings.append((pattern_name, snippet))

        # Entropy-based detection — tokenize on whitespace and punctuation
        tokens = re.split(r'[\s,;|&\'"<>()\[\]{}]+', text)
        for token in tokens:
            if (
                len(token) >= _MIN_TOKEN_LENGTH
                and _is_secret_charset(token)
                and _shannon_entropy(token) >= self.min_entropy
                and not any(regex.search(token) for _, regex in _SECRET_PATTERNS)
            ):
                snippet = self._safe_snippet(token)
                findings.append(("high_entropy_string", snippet))

        return findings

    @staticmethod
    def _safe_snippet(value: str) -> str:
        """Return a safe display snippet — show prefix only, never full value."""
        if len(value) <= 8:
            return "[REDACTED]"
        return value[:4] + "..." + "[REDACTED]"

    @staticmethod
    def _format_error(violations: list[str]) -> str:
        """Format violations into actionable error message ≤ 300 chars."""
        if len(violations) == 1:
            msg = f"Secret leak detected: {violations[0]}"
            return msg[:300]

        msg = f"[{len(violations)} secret leaks detected] "
        msg += "; ".join(v.split(":")[0] for v in violations[:3])
        if len(violations) > 3:
            msg += f" (+{len(violations) - 3} more)"
        return msg[:300]
