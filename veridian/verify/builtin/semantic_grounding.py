"""
veridian.verify.builtin.semantic_grounding
──────────────────────────────────────────
SemanticGroundingVerifier — Gap 1 implementation.

RESEARCH BASIS:
  Semantic Grounding as Hallucination Mitigation (OpenReview QYrzaPAqnX, 2025):
    "Hallucinations in enterprise AI agents arise at perception, not generation.
     Semantic grounding of enterprise schemas constrains agent perception, reduces
     overconfident actions on ambiguous inputs, and acts as an effective hallucination
     mitigation layer for safe autonomous operation."

  LLM-based Agents Hallucination Survey (arXiv 2509.18970):
    Self-consistency, self-questioning, and cross-field validation are the most
    effective lightweight hallucination mitigations that don't require LLM calls.

PURPOSE:
  Runs BEFORE domain verifiers in a CompositeVerifier chain.
  Catches three classes of error that slip past syntactic verifiers:

  Class A — Cross-field logical inconsistency
    e.g. risk_level="LOW" but clause_type="change_of_control" (almost always HIGH/CRITICAL)
    e.g. status="compliant" but violated_policies is non-empty

  Class B — Implicit numerical/identifier claim drift
    e.g. page_number=42 but the document only has 38 pages
    e.g. line_number=500 in a 200-line file

  Class C — Summary–structured divergence
    e.g. summary says "no issues found" but structured has risk_level="CRITICAL"
    e.g. summary says "file created" but artifacts list is empty

USAGE:
  # Prepend to any CompositeVerifier chain:
  verifier_config={
      "verifiers": [
          {"id": "semantic_grounding", "config": {
              "consistency_rules": [
                  {"if_field": "status", "equals": "compliant",
                   "then_field": "violated_policies", "must_be_empty": True}
              ],
              "range_checks": [
                  {"field": "page_number", "min": 1,
                   "max_from_metadata": "total_pages"}
              ],
              "summary_keywords": {
                  "no issues": {"structured_field": "risk_level",
                                "must_not_equal": "CRITICAL"}
              }
          }},
          {"id": "legal_clause"},   # domain verifier follows
      ]
  }

DESIGN RULES (never violate):
  - Never calls LLM
  - Stateless and idempotent
  - All checks are deterministic given inputs
  - Errors are specific + actionable + < 300 chars
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


@dataclass
class ConsistencyRule:
    """If field A equals value V, then field B must satisfy a condition."""

    if_field: str
    equals: Any
    then_field: str
    must_be_empty: bool = False  # then_field must be None / [] / ""
    must_not_be_empty: bool = False  # then_field must be non-empty
    must_equal: Any | None = None  # then_field must equal this value
    must_not_equal: Any | None = None  # then_field must NOT equal this value
    must_be_in: list[Any] | None = None  # then_field must be in this list


@dataclass
class RangeCheck:
    """A numerical field must fall within bounds."""

    field: str
    min: float | None = None
    max: float | None = None
    max_from_metadata: str | None = None  # key in task.metadata for dynamic max


@dataclass
class SummaryKeyword:
    """If summary contains keyword, structured field must satisfy condition."""

    keyword: str  # case-insensitive
    structured_field: str
    must_be_empty: bool = False
    must_not_equal: Any | None = None
    must_equal: Any | None = None


class SemanticGroundingVerifier(BaseVerifier):
    """
    Lightweight semantic grounding layer.
    Catches cross-field inconsistencies, range violations, and
    summary–structured divergence without calling an LLM.

    Designed to run first in a CompositeVerifier chain.
    """

    id = "semantic_grounding"
    description = (
        "Checks internal consistency of structured output before domain verification. "
        "Catches cross-field conflicts, range violations, and summary–structured drift."
    )

    # ── Built-in consistency rules for common patterns ────────────────────────
    # Domain-specific rules should be passed via constructor config.
    _BUILTIN_RULES: list[ConsistencyRule] = [
        # If decision is ALLOW/approve, there should be no violated_policies
        ConsistencyRule(
            if_field="decision", equals="ALLOW", then_field="violated_policies", must_be_empty=True
        ),
        ConsistencyRule(
            if_field="status",
            equals="compliant",
            then_field="violated_policies",
            must_be_empty=True,
        ),
        # If ESCALATE/FLAG, reasoning must be present
        ConsistencyRule(
            if_field="decision", equals="ESCALATE", then_field="reasoning", must_not_be_empty=True
        ),
        ConsistencyRule(
            if_field="decision",
            equals="REMOVE",
            then_field="violated_policies",
            must_not_be_empty=True,
        ),
        # If type is none_found, no quote should be present
        ConsistencyRule(
            if_field="clause_type", equals="none_found", then_field="quote", must_be_empty=True
        ),
    ]

    def __init__(
        self,
        consistency_rules: list[dict[str, Any]] | None = None,
        range_checks: list[dict[str, Any]] | None = None,
        summary_keywords: dict[str, Any] | None = None,
        required_if_not_none_found: list[str] | None = None,
        check_artifacts_match_summary: bool = True,
        check_empty_structured: bool = True,
    ) -> None:
        """
        Args:
            consistency_rules: List of ConsistencyRule dicts (see class docstring).
            range_checks: List of RangeCheck dicts for numerical bounds.
            summary_keywords: Dict mapping keyword → condition on structured field.
            required_if_not_none_found: Fields required unless a "none_found" escape
                hatch is present in structured output.
            check_artifacts_match_summary: If True, check summary mentions of file
                creation are backed by non-empty artifacts list.
            check_empty_structured: If True, fail when structured is completely empty.
        """
        self.rules: list[ConsistencyRule] = list(self._BUILTIN_RULES)
        if consistency_rules:
            for r in consistency_rules:
                self.rules.append(ConsistencyRule(**r))

        self.range_checks: list[RangeCheck] = []
        if range_checks:
            for r in range_checks:
                self.range_checks.append(RangeCheck(**r))

        self.summary_keywords: list[SummaryKeyword] = []
        if summary_keywords:
            for kw, cond in summary_keywords.items():
                self.summary_keywords.append(SummaryKeyword(keyword=kw, **cond))

        self.required_if_not_none_found = required_if_not_none_found or []
        self.check_artifacts_match_summary = check_artifacts_match_summary
        self.check_empty_structured = check_empty_structured

    # ── Public interface ──────────────────────────────────────────────────────

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        s = result.structured
        summary = result.raw_output.lower()

        # 1. Empty structured output
        if self.check_empty_structured and not s:
            return self._fail(
                "structured output is completely empty. "
                "Output a valid JSON object inside <veridian:result>."
            )

        # 2. Cross-field consistency rules
        for rule in self.rules:
            verdict = self._check_rule(rule, s)
            if verdict:
                return self._fail(verdict)

        # 3. Range checks
        for rc in self.range_checks:
            verdict = self._check_range(rc, s, task)
            if verdict:
                return self._fail(verdict)

        # 4. Summary–structured divergence checks
        for sk in self.summary_keywords:
            verdict = self._check_summary_keyword(sk, summary, s)
            if verdict:
                return self._fail(verdict)

        # 5. Built-in summary divergence patterns
        verdict = self._check_builtin_summary_patterns(summary, result)
        if verdict:
            return self._fail(verdict)

        # 6. Required fields conditional check
        if self.required_if_not_none_found:
            none_found = any(
                str(v).lower() in ("none_found", "none", "not_found") for v in s.values()
            )
            if not none_found:
                missing = [f for f in self.required_if_not_none_found if not s.get(f)]
                if missing:
                    return self._fail(
                        f"Required fields missing when result is not 'none_found': "
                        f"{missing}. Add them to structured output."
                    )

        return VerificationResult(
            passed=True,
            evidence={"grounding_checks": "all passed"},
        )

    # ── Rule checkers ─────────────────────────────────────────────────────────

    def _check_rule(self, rule: ConsistencyRule, s: dict[str, Any]) -> str | None:
        """Return error string if rule violated, else None."""
        if rule.if_field not in s:
            return None
        if s[rule.if_field] != rule.equals:
            return None

        val = s.get(rule.then_field)

        if rule.must_be_empty and val not in (None, "", [], {}):
            return (
                f"When {rule.if_field}='{rule.equals}', "
                f"{rule.then_field} must be empty (got {repr(val)[:60]}). "
                f"Fix the {rule.then_field} field."
            )

        if rule.must_not_be_empty and val in (None, "", [], {}):
            return (
                f"When {rule.if_field}='{rule.equals}', "
                f"{rule.then_field} must not be empty. "
                f"Provide a value for {rule.then_field}."
            )

        if rule.must_equal is not None and val != rule.must_equal:
            return (
                f"When {rule.if_field}='{rule.equals}', "
                f"{rule.then_field} must be '{rule.must_equal}' (got '{val}'). "
                f"Fix {rule.then_field}."
            )

        if rule.must_not_equal is not None and val == rule.must_not_equal:
            return (
                f"When {rule.if_field}='{rule.equals}', "
                f"{rule.then_field} must NOT be '{rule.must_not_equal}'. "
                f"Fix {rule.then_field}."
            )

        if rule.must_be_in is not None and val not in rule.must_be_in:
            return (
                f"When {rule.if_field}='{rule.equals}', "
                f"{rule.then_field} must be one of {rule.must_be_in} (got '{val}'). "
                f"Fix {rule.then_field}."
            )

        return None

    def _check_range(self, rc: RangeCheck, s: dict[str, Any], task: Task) -> str | None:
        """Return error string if range check violated, else None."""
        if rc.field not in s:
            return None
        try:
            val = float(s[rc.field])
        except (TypeError, ValueError):
            return (
                f"Field '{rc.field}' must be a number (got '{s[rc.field]}'). "
                f"Provide a valid numeric value."
            )

        if rc.min is not None and val < rc.min:
            return (
                f"Field '{rc.field}' value {val} is below minimum {rc.min}. "
                f"Check the source document and correct the value."
            )

        effective_max = rc.max
        if rc.max_from_metadata and rc.max_from_metadata in task.metadata:
            with contextlib.suppress(TypeError, ValueError):
                effective_max = float(task.metadata[rc.max_from_metadata])

        if effective_max is not None and val > effective_max:
            return (
                f"Field '{rc.field}' value {val} exceeds maximum {effective_max}. "
                f"Check the source document — the value may be incorrect."
            )

        return None

    def _check_summary_keyword(
        self, sk: SummaryKeyword, summary: str, s: dict[str, Any]
    ) -> str | None:
        """Return error string if summary keyword triggers a violated condition."""
        if sk.keyword.lower() not in summary:
            return None
        if sk.structured_field not in s:
            return None

        val = s[sk.structured_field]

        if sk.must_be_empty and val not in (None, "", [], {}):
            return (
                f"Summary says '{sk.keyword}' but {sk.structured_field}='{val}'. "
                f"Summary and structured output are contradictory. Fix one of them."
            )
        if sk.must_not_equal is not None and val == sk.must_not_equal:
            return (
                f"Summary says '{sk.keyword}' but {sk.structured_field}='{val}'. "
                f"These are contradictory. Fix the structured output to match the summary."
            )
        if sk.must_equal is not None and val != sk.must_equal:
            return (
                f"Summary says '{sk.keyword}' but {sk.structured_field}='{val}' "
                f"(expected '{sk.must_equal}'). Fix the structured output."
            )

        return None

    def _check_builtin_summary_patterns(self, summary: str, result: TaskResult) -> str | None:
        """Check common summary–artifact divergence patterns."""
        if not self.check_artifacts_match_summary:
            return None

        file_creation_phrases = (
            "created file",
            "wrote file",
            "generated file",
            "saved file",
            "output file",
            "created the file",
        )
        mentions_file = any(p in summary for p in file_creation_phrases)
        if mentions_file and not result.artifacts:
            return (
                "Summary mentions file creation but artifacts list is empty. "
                "Add the created file path(s) to the artifacts list in your result."
            )

        no_issues_phrases = (
            "no issues found",
            "no problems found",
            "clean document",
            "nothing to report",
            "no findings",
        )
        mentions_no_issues = any(p in summary for p in no_issues_phrases)
        if mentions_no_issues:
            s = result.structured
            high_risk = s.get("risk_level") in ("HIGH", "CRITICAL")
            bad_status = s.get("status") in ("gap", "FLAG", "REMOVE", "ESCALATE")
            if high_risk or bad_status:
                return (
                    "Summary says 'no issues' but structured output indicates "
                    f"risk_level='{s.get('risk_level')}' or status='{s.get('status')}'. "
                    "Fix the contradiction."
                )

        return None

    @staticmethod
    def _fail(msg: str) -> VerificationResult:
        return VerificationResult(passed=False, error=f"[semantic grounding] {msg}"[:300])
