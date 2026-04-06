"""
veridian.secrets.pii_policy
────────────────────────────
WCP-026: Unified PII detection and redaction policy.

Provides pattern-based detection for common PII types (SSN, email, credit card,
phone, API keys, bearer tokens) and a redaction engine that replaces matches
with type-tagged placeholders.

Usage::

    policy = PIIPolicy()               # uses BUILTIN_PATTERNS
    matches = policy.detect(text)       # -> list[PIIMatch]
    clean = policy.redact(text)         # -> str with [REDACTED-TYPE] tokens

Custom patterns can be added/removed at runtime via
``policy.add_pattern()`` / ``policy.remove_pattern()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

__all__ = [
    "BUILTIN_PATTERNS",
    "PIIMatch",
    "PIIPattern",
    "PIIPolicy",
]


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PIIMatch:
    """A single PII match found in text."""

    pattern_name: str
    start: int
    end: int
    original: str


@dataclass(frozen=True, slots=True)
class PIIPattern:
    """A named PII detection pattern with a compiled regex and replacement tag."""

    name: str
    regex: re.Pattern[str]
    replacement: str


# ── Built-in patterns ────────────────────────────────────────────────────────

# SSN: exactly 3-2-4 digit groups, bounded by non-digit or string edges.
# The negative lookbehind/lookahead avoids matching inside longer digit
# sequences (phone numbers, version strings, etc.).
_SSN_RE = re.compile(
    r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
)

# Email: standard local@domain.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

# Credit card: 13-19 digits with optional dash/space separators.
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ \-]?){13,19}\b",
)

# Phone (US): optional country code, area code, 3-4 digit groups.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+1[\s\-]?)?(?:\(\d{3}\)|\d{3})[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)",
)

# API keys: OpenAI sk-*, Anthropic sk-ant-*, AWS AKIA*, GitHub ghp_/gho_/ghs_/ghr_
_API_KEY_RE = re.compile(
    r"(?:"
    r"sk-(?:proj-)?[A-Za-z0-9]{20,}"  # OpenAI
    r"|sk-ant-(?:api\d+-)?[A-Za-z0-9\-_]{20,}"  # Anthropic
    r"|\bAKIA[0-9A-Z]{16}\b"  # AWS
    r"|\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"  # GitHub
    r")",
    re.IGNORECASE,
)

# Bearer tokens in authorization headers.
_BEARER_RE = re.compile(
    r"(?i)bearer\s+[A-Za-z0-9\-_=.+/]{20,}",
)


BUILTIN_PATTERNS: list[PIIPattern] = [
    PIIPattern(name="ssn", regex=_SSN_RE, replacement="[REDACTED-SSN]"),
    PIIPattern(name="email", regex=_EMAIL_RE, replacement="[REDACTED-EMAIL]"),
    PIIPattern(name="credit_card", regex=_CREDIT_CARD_RE, replacement="[REDACTED-CREDIT_CARD]"),
    PIIPattern(name="phone", regex=_PHONE_RE, replacement="[REDACTED-PHONE]"),
    PIIPattern(name="api_key", regex=_API_KEY_RE, replacement="[REDACTED-API_KEY]"),
    PIIPattern(name="bearer_token", regex=_BEARER_RE, replacement="[REDACTED-BEARER_TOKEN]"),
]


# ── PIIPolicy ────────────────────────────────────────────────────────────────


class PIIPolicy:
    """Unified PII detection and redaction engine.

    Stateless aside from the mutable pattern list.  Thread-safe for
    concurrent ``detect()`` / ``redact()`` calls as long as ``add_pattern``
    / ``remove_pattern`` are not called concurrently with reads.
    """

    _default_patterns: ClassVar[list[PIIPattern]] = BUILTIN_PATTERNS

    def __init__(self, patterns: list[PIIPattern] | None = None) -> None:
        """Initialise with explicit patterns or the built-in set.

        Args:
            patterns: If provided, used instead of BUILTIN_PATTERNS.
        """
        self._patterns: list[PIIPattern] = list(patterns or self._default_patterns)

    # ── Detection ────────────────────────────────────────────────────────────

    def detect(self, text: str) -> list[PIIMatch]:
        """Scan *text* for PII and return all matches with positions.

        Returns:
            Ordered list of :class:`PIIMatch` (by start position).
        """
        matches: list[PIIMatch] = []
        for pattern in self._patterns:
            for m in pattern.regex.finditer(text):
                matches.append(
                    PIIMatch(
                        pattern_name=pattern.name,
                        start=m.start(),
                        end=m.end(),
                        original=m.group(0),
                    )
                )
        matches.sort(key=lambda x: x.start)
        return matches

    # ── Redaction ────────────────────────────────────────────────────────────

    def redact(self, text: str) -> str:
        """Return a copy of *text* with all PII replaced by ``[REDACTED-TYPE]`` tags.

        Applies patterns in priority order (first pattern wins for overlapping
        regions).  Non-sensitive text is preserved verbatim.
        """
        result = text
        # Apply patterns in reverse-match-position order to keep indices valid
        # after each substitution (longest first to handle overlapping patterns).
        all_matches = self.detect(text)
        # Deduplicate overlapping matches: keep longest match per position
        used_ranges: list[tuple[int, int]] = []
        unique: list[PIIMatch] = []
        for m in all_matches:
            overlap = False
            for start, end in used_ranges:
                if m.start < end and m.end > start:
                    overlap = True
                    break
            if not overlap:
                unique.append(m)
                used_ranges.append((m.start, m.end))

        # Build replacement map keyed by pattern_name
        replacement_map: dict[str, str] = {p.name: p.replacement for p in self._patterns}

        # Replace in reverse order to keep positions stable
        for m in reversed(unique):
            tag = replacement_map.get(m.pattern_name, "[REDACTED]")
            result = result[: m.start] + tag + result[m.end :]
        return result

    # ── Pattern management ───────────────────────────────────────────────────

    def add_pattern(self, pattern: PIIPattern) -> None:
        """Append a custom pattern to the detection set."""
        self._patterns.append(pattern)

    def remove_pattern(self, name: str) -> None:
        """Remove a pattern by name.  No-op if name not found."""
        self._patterns = [p for p in self._patterns if p.name != name]
