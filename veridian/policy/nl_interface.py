"""
veridian.policy.nl_interface
──────────────────────────────
Natural Language Policy Interface.

Converts natural language compliance rules into structured Policy-as-Code
(PolicySpec) with a mandatory human-review step before activation.

Design:
- The LLM translator is injected (not hard-coded) — testable without a real LLM.
- PolicyStore uses atomic writes (CLAUDE.md §1.3).
- All errors raise from the VeridianError hierarchy.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from veridian.core.exceptions import NLPolicyError, PolicyNotFound

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────


class PolicySeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class PolicyStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    REJECTED = "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# POLICY CHECK
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PolicyCheck:
    """
    A single condition within a PolicySpec.

    field    — dotted path into the task result (e.g. "output.text")
    operator — comparison operator (e.g. "not_contains", "eq", "matches", "lt")
    value    — the value to compare against
    error_message — optional custom error displayed on failure
    """

    field: str
    operator: str
    value: Any
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyCheck:
        return cls(
            field=d["field"],
            operator=d["operator"],
            value=d["value"],
            error_message=d.get("error_message", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
# POLICY SPEC
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PolicySpec:
    """
    Structured policy definition that can be compiled to a Python verifier.

    Compatible with the Policy-as-Code Engine (F2.5) YAML schema.
    """

    rule_id: str
    description: str
    checks: list[PolicyCheck]
    severity: PolicySeverity

    # ── YAML serialisation ────────────────────────────────────────────────────

    def to_yaml(self) -> str:
        """Emit a YAML representation of this PolicySpec."""
        lines = [
            f"rule_id: {self.rule_id}",
            f"description: {self.description}",
            f"severity: {self.severity.value}",
            "checks:",
        ]
        for check in self.checks:
            lines.append(f"  - field: {check.field}")
            lines.append(f"    operator: {check.operator}")
            lines.append(f"    value: {check.value!r}")
            if check.error_message:
                lines.append(f"    error_message: {check.error_message!r}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_yaml(cls, yaml_str: str) -> PolicySpec:
        """Parse a YAML string into a PolicySpec."""
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            # Fallback: minimal YAML parser for simple cases
            return cls._parse_yaml_minimal(yaml_str)
        data = yaml.safe_load(yaml_str)
        return cls._from_data(data)

    @classmethod
    def _from_data(cls, data: dict[str, Any]) -> PolicySpec:
        checks = [PolicyCheck.from_dict(c) for c in data.get("checks", [])]
        return cls(
            rule_id=data["rule_id"],
            description=data.get("description", ""),
            checks=checks,
            severity=PolicySeverity(data.get("severity", "blocking")),
        )

    @classmethod
    def _parse_yaml_minimal(cls, yaml_str: str) -> PolicySpec:
        """Minimal YAML parser for the PolicySpec subset (no PyYAML dep)."""

        rule_id = ""
        description = ""
        severity = PolicySeverity.BLOCKING
        checks: list[PolicyCheck] = []

        for line in yaml_str.splitlines():
            stripped = line.strip()
            if stripped.startswith("rule_id:"):
                rule_id = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("description:"):
                description = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("severity:"):
                raw = stripped.split(":", 1)[1].strip()
                severity = PolicySeverity(raw)

        # Parse checks block — simple state machine
        in_checks = False
        current: dict[str, Any] = {}
        for line in yaml_str.splitlines():
            stripped = line.strip()
            if stripped == "checks:":
                in_checks = True
                continue
            if in_checks:
                if stripped.startswith("- "):
                    if current:
                        checks.append(PolicyCheck.from_dict(current))
                    key, _, val = stripped[2:].partition(":")
                    current = {key.strip(): val.strip()}
                elif stripped.startswith("- ") or ":" in stripped:
                    key, _, val = stripped.partition(":")
                    current[key.strip()] = val.strip()

        if current and "field" in current:
            checks.append(PolicyCheck.from_dict(current))

        return cls(
            rule_id=rule_id,
            description=description,
            checks=checks,
            severity=severity,
        )

    # ── Dict serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "severity": self.severity.value,
            "checks": [c.to_dict() for c in self.checks],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicySpec:
        checks = [PolicyCheck.from_dict(c) for c in d.get("checks", [])]
        return cls(
            rule_id=d["rule_id"],
            description=d.get("description", ""),
            checks=checks,
            severity=PolicySeverity(d.get("severity", "blocking")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# POLICY DRAFT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PolicyDraft:
    """
    A policy pending human review.

    Lifecycle: PENDING_REVIEW → ACTIVE (approved) or REJECTED.
    A PolicyDraft must be activated before it can be used in verification.
    """

    draft_id: str
    nl_input: str
    spec: PolicySpec
    status: PolicyStatus = PolicyStatus.PENDING_REVIEW
    rejection_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def approve(self) -> None:
        """Human approves — policy becomes ACTIVE."""
        self.status = PolicyStatus.ACTIVE
        log.info("policy_draft.approve id=%s rule=%s", self.draft_id, self.spec.rule_id)

    def reject(self, reason: str = "") -> None:
        """Human rejects — policy moves to REJECTED."""
        self.status = PolicyStatus.REJECTED
        self.rejection_reason = reason
        log.info("policy_draft.reject id=%s reason=%s", self.draft_id, reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "nl_input": self.nl_input,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyDraft:
        return cls(
            draft_id=d["draft_id"],
            nl_input=d["nl_input"],
            spec=PolicySpec.from_dict(d["spec"]),
            status=PolicyStatus(d.get("status", "pending_review")),
            rejection_reason=d.get("rejection_reason", ""),
            created_at=d.get("created_at", datetime.now(UTC).isoformat()),
        )


# ─────────────────────────────────────────────────────────────────────────────
# POLICY STORE  (atomic writes — CLAUDE.md §1.3)
# ─────────────────────────────────────────────────────────────────────────────


class PolicyStore:
    """
    Persistent store for PolicyDraft objects.  JSON-backed with atomic writes.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def save(self, draft: PolicyDraft) -> None:
        """Insert or update a PolicyDraft."""
        all_drafts = {d.draft_id: d.to_dict() for d in self.list_all()}
        all_drafts[draft.draft_id] = draft.to_dict()
        self._atomic_write(all_drafts)

    def get(self, draft_id: str) -> PolicyDraft:
        data = self._load_raw()
        if draft_id not in data:
            raise PolicyNotFound(draft_id)
        return PolicyDraft.from_dict(data[draft_id])

    def list_all(self) -> list[PolicyDraft]:
        return [PolicyDraft.from_dict(d) for d in self._load_raw().values()]

    def list_active(self) -> list[PolicyDraft]:
        return [d for d in self.list_all() if d.status == PolicyStatus.ACTIVE]

    def _load_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as f:
                result: dict[str, Any] = json.load(f)
                return result
        except (json.JSONDecodeError, OSError):
            return {}

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._path.parent, delete=False, suffix=".tmp"
        ) as f:
            json.dump(data, f, indent=2)
            tmp = Path(f.name)
        os.replace(tmp, self._path)


# ─────────────────────────────────────────────────────────────────────────────
# NL POLICY INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

# Type alias for the injected translator callable
TranslatorFn = Callable[[str], PolicySpec]


class NLPolicyInterface:
    """
    Translates natural language compliance rules into PolicySpec drafts.

    The translator is injected — in production pass an LLM-backed function;
    in tests pass a mock translator.  If no translator is provided and
    ``translate()`` is called, NLPolicyError is raised.

    Workflow:
      1. translate(nl) → PolicyDraft (status: PENDING_REVIEW)
      2. Human reviews — either activate(draft_id) or reject(draft_id)
      3. Active policies can be retrieved via list_policies(status=ACTIVE)
    """

    def __init__(
        self,
        store: PolicyStore,
        translator: TranslatorFn | None = None,
    ) -> None:
        self._store = store
        self._translator = translator

    def translate(self, nl: str) -> PolicyDraft:
        """
        Translate a natural language rule into a PolicyDraft pending review.

        Raises NLPolicyError if no translator is configured.
        """
        if self._translator is None:
            raise NLPolicyError(
                "No translator configured. Inject a translator callable "
                "(e.g. an LLM-backed function) via NLPolicyInterface(translator=...)."
            )

        spec = self._translator(nl)
        draft_id = str(uuid.uuid4())
        draft = PolicyDraft(draft_id=draft_id, nl_input=nl, spec=spec)
        self._store.save(draft)
        log.info("nl_policy.translate draft=%s rule=%s", draft_id, spec.rule_id)
        return draft

    def activate(self, draft_id: str) -> PolicyDraft:
        """
        Human approves a draft — transitions it to ACTIVE.

        Raises PolicyNotFound if draft_id not found.
        """
        draft = self._store.get(draft_id)  # raises PolicyNotFound if absent
        draft.approve()
        self._store.save(draft)
        return draft

    def reject(self, draft_id: str, reason: str = "") -> PolicyDraft:
        """
        Human rejects a draft.

        Raises PolicyNotFound if draft_id not found.
        """
        draft = self._store.get(draft_id)
        draft.reject(reason=reason)
        self._store.save(draft)
        return draft

    def explain(self, draft_id: str) -> str:
        """
        Return a plain-English explanation of what a policy does.

        Raises PolicyNotFound if draft_id not found.
        """
        draft = self._store.get(draft_id)
        spec = draft.spec
        lines = [
            f"Policy '{spec.rule_id}' ({spec.severity.value}): {spec.description}",
            "",
            f'Original natural language: "{draft.nl_input}"',
            f"Status: {draft.status.value}",
            "",
        ]
        if spec.checks:
            lines.append("This policy enforces the following checks:")
            for i, check in enumerate(spec.checks, 1):
                msg = (
                    check.error_message
                    or f"field '{check.field}' must satisfy {check.operator} '{check.value}'"
                )
                lines.append(f"  {i}. {msg}")
        else:
            lines.append("This policy has no configured checks.")

        return "\n".join(lines)

    def list_policies(self, status: PolicyStatus | None = None) -> list[PolicyDraft]:
        """Return all policies, optionally filtered by status."""
        all_drafts = self._store.list_all()
        if status is None:
            return all_drafts
        return [d for d in all_drafts if d.status == status]
