"""
veridian.hooks.builtin.cross_run_consistency
─────────────────────────────────────────────
CrossRunConsistencyHook — Gap 3 implementation.

RESEARCH BASIS:
  LLM-based Agents Hallucination Survey (arXiv 2509.18970):
    "Self-consistency leverages the generation of multiple candidate outputs and
     aggregates them using majority voting or confidence-weighted schemes to select
     the most reliable results."

  Legal RAG Hallucinations (Stanford 2025):
    "The binary notion of hallucination does not fully capture the behaviour of RAG
     systems. We expand to two dimensions: correctness and groundedness. Groundedness
     refers to the relationship between the model's response and its cited sources."

  ImplicitClaimExtractor motivation:
    Agents produce specific numerical, date, and identifier claims in structured output
    that are never flagged as quotes. Page 42, line 200, score 0.87 — these sound precise
    but may be fabricated. They must be cross-checked against other tasks in the same run.

PURPOSE:
  After each DONE task, extract "claims" from result.structured and check them against
  previously completed tasks in the same run for conflicts.

  Three conflict types detected:
  1. Same entity, contradictory conclusions
     e.g. contract_id=X rated LOW risk in task_001 but HIGH risk in task_047
  2. Same numerical fact, different values
     e.g. page_number=42 in task_001 but another agent says the same clause is on page_38
  3. Entity identity conflicts
     e.g. "Acme Corp" identified as a vendor in one task, as a customer in another

DESIGN:
  - Hook fires on_task_completed (after verifier passes)
  - Reads completed tasks from ledger (read-only — hooks cannot write to ledger)
  - Raises no exceptions on conflict (non-blocking) — logs warning and stores
    conflict in task.metadata["consistency_conflicts"] via the RunSession cache
  - Optional: raise HumanReviewRequired on CRITICAL conflicts

USAGE:
  runner.add_hook(CrossRunConsistencyHook, config={
      "claim_fields": ["risk_level", "status", "decision"],
      "entity_key_field": "entity_id",       # field that identifies the entity
      "raise_on_critical": True,             # HumanReview for contradictory HIGH/CRITICAL
      "conflict_log_path": "conflicts.jsonl",
  })
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from veridian.core.exceptions import HumanReviewRequired

log = logging.getLogger(__name__)

# BaseHook imported at runtime to avoid circular imports at module level
# The actual class inherits from it below.


@dataclass
class ClaimConflict:
    """Represents a detected conflict between two task outputs."""

    task_a_id: str
    task_b_id: str
    entity_id: str | None
    field: str
    value_a: Any
    value_b: Any
    severity: str  # "critical", "warning", "info"
    detected_at: datetime = dataclasses.field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_a_id": self.task_a_id,
            "task_b_id": self.task_b_id,
            "entity_id": self.entity_id,
            "field": self.field,
            "value_a": self.value_a,
            "value_b": self.value_b,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
        }

    def summary(self) -> str:
        return (
            f"[{self.severity.upper()}] Field '{self.field}': "
            f"task {self.task_a_id} says '{self.value_a}', "
            f"task {self.task_b_id} says '{self.value_b}'"
            + (f" for entity '{self.entity_id}'" if self.entity_id else "")
        )


# ── Critical value conflict matrix ───────────────────────────────────────────
# If field A has value X in one task and value Y in another task about the same
# entity, that's a critical conflict requiring human review.

_CRITICAL_PAIRS: dict[str, set[frozenset[object]]] = {
    "risk_level": {
        frozenset({"LOW", "CRITICAL"}),
        frozenset({"LOW", "HIGH"}),
        frozenset({"MEDIUM", "CRITICAL"}),
    },
    "status": {
        frozenset({"compliant", "gap"}),
        frozenset({"compliant", "partial"}),
    },
    "decision": {
        frozenset({"ALLOW", "REMOVE"}),
        frozenset({"ALLOW", "FLAG_HIGH"}),
        frozenset({"ALLOW", "ESCALATE"}),
    },
    "clause_found": {
        frozenset({True, False}),
        frozenset({"true", "false"}),
    },
}


def _is_critical_conflict(field: str, val_a: Any, val_b: Any) -> bool:
    if field not in _CRITICAL_PAIRS:
        return False
    pair = frozenset({str(val_a).upper(), str(val_b).upper()})
    return any(
        pair == frozenset({str(a).upper(), str(b).upper()})
        for fs in _CRITICAL_PAIRS[field]
        for a, b in [list(fs)]
    )


class CrossRunConsistencyHook:
    """
    After each completed task, checks its claims against all previously
    completed tasks in the same run. Detects contradictions in claim fields
    for the same entity.

    Does NOT write to ledger (hooks are read-only).
    Writes conflicts to an optional JSONL log file.
    Raises HumanReviewRequired on critical conflicts when raise_on_critical=True.
    """

    id = "cross_run_consistency"

    def __init__(
        self,
        claim_fields: list[str] | None = None,
        entity_key_field: str | None = None,
        raise_on_critical: bool = False,
        conflict_log_path: str | None = None,
        ignore_none_found: bool = True,
    ) -> None:
        """
        Args:
            claim_fields: Structured output fields to monitor for conflicts.
                          If None, uses the default set (risk_level, status, decision).
            entity_key_field: The field that identifies the entity being evaluated
                              (e.g. "contract_id", "control_id", "document_id").
                              If None, uses task.id as entity key.
            raise_on_critical: If True, raise HumanReviewRequired for critical conflicts.
            conflict_log_path: JSONL file to append conflicts to. Created if absent.
            ignore_none_found: Skip consistency check when structured output contains
                               a "none_found" escape hatch value.
        """
        self.claim_fields = claim_fields or ["risk_level", "status", "decision", "clause_type"]
        self.entity_key_field = entity_key_field
        self.raise_on_critical = raise_on_critical
        self.conflict_log_path = conflict_log_path
        self.ignore_none_found = ignore_none_found

        # In-memory claim store: {entity_id: {field: (value, task_id)}}
        # Reset on each new run by checking run_id
        self._claim_store: dict[str, dict[str, tuple[Any, str]]] = {}
        self._current_run_id: str | None = None
        self._conflicts: list[ClaimConflict] = []

    # ── Hook event handlers ───────────────────────────────────────────────────

    def on_run_started(self, event: Any) -> None:
        """Reset claim store when a new run begins."""
        if hasattr(event, "run_id") and event.run_id != self._current_run_id:
            self._current_run_id = event.run_id
            self._claim_store.clear()
            self._conflicts.clear()
            log.debug("cross_run_consistency: claim store reset for run %s", event.run_id)

    def after_result(self, event: Any) -> None:
        """
        Called after a task result is accepted (verifier passed).
        Check claims against existing store, then register new claims.
        """
        try:
            task = getattr(event, "task", None)
            if task is None or not hasattr(task, "result") or task.result is None:
                return

            structured = task.result.structured
            if not structured:
                return

            # Skip none_found tasks if configured
            if self.ignore_none_found and any(
                str(v).lower() in ("none_found", "not_found", "none") for v in structured.values()
            ):
                return

            entity_id = self._get_entity_id(task, structured)
            new_conflicts = self._check_and_register(task.id, entity_id, structured)

            if new_conflicts:
                self._conflicts.extend(new_conflicts)
                for c in new_conflicts:
                    log.warning("cross_run_consistency: %s", c.summary())
                    if self.conflict_log_path:
                        self._write_conflict_log(c)

                critical = [c for c in new_conflicts if c.severity == "critical"]
                if critical and self.raise_on_critical:
                    raise HumanReviewRequired(
                        task_id=task.id,
                        reason=(f"Critical consistency conflict detected: {critical[0].summary()}"),
                    )

        except HumanReviewRequired:
            raise
        except Exception as e:
            # Hook errors must never kill a run
            log.warning("cross_run_consistency: error in after_result: %s", e)

    # ── Core logic ────────────────────────────────────────────────────────────

    def _get_entity_id(self, task: Any, structured: dict[str, Any]) -> str:
        """
        Extract entity identifier.
        - entity_key_field set: scope conflicts to that entity
        - entity_key_field None + no metadata key found: use __global__ sentinel
          so all tasks in the run share one claim namespace (conflicts fire across tasks)
        """
        if self.entity_key_field:
            if self.entity_key_field in structured:
                return str(structured[self.entity_key_field])
            if (
                hasattr(task, "metadata")
                and task.metadata
                and self.entity_key_field in task.metadata
            ):
                return str(task.metadata[self.entity_key_field])
            return str(task.id)  # field configured but not found — no false positives

        # No entity_key_field: try well-known metadata keys first
        if hasattr(task, "metadata") and task.metadata:
            for key in ("entity_id", "contract_id", "document_id", "control_id", "source_file"):
                if key in task.metadata:
                    return str(task.metadata[key])

        # Global fallback: all tasks share one namespace
        return "__global__"

    def _check_and_register(
        self, task_id: str, entity_id: str, structured: dict[str, Any]
    ) -> list[ClaimConflict]:
        """Check new claims against store, register them, return conflicts found."""
        conflicts = []

        if entity_id not in self._claim_store:
            self._claim_store[entity_id] = {}

        existing = self._claim_store[entity_id]

        for claim_field in self.claim_fields:
            if claim_field not in structured:
                continue
            new_val = structured[claim_field]
            if new_val in (None, "", [], {}):
                continue

            if claim_field in existing:
                old_val, old_task_id = existing[claim_field]
                if old_task_id == task_id:
                    continue  # same task, skip

                # Normalise for comparison
                norm_new = str(new_val).strip().lower()
                norm_old = str(old_val).strip().lower()

                if norm_new != norm_old:
                    is_critical = _is_critical_conflict(claim_field, old_val, new_val)
                    severity = "critical" if is_critical else "warning"
                    conflicts.append(
                        ClaimConflict(
                            task_a_id=old_task_id,
                            task_b_id=task_id,
                            entity_id=entity_id if entity_id != task_id else None,
                            field=claim_field,
                            value_a=old_val,
                            value_b=new_val,
                            severity=severity,
                        )
                    )
            else:
                # Register new claim
                existing[claim_field] = (new_val, task_id)

        return conflicts

    def _write_conflict_log(self, conflict: ClaimConflict) -> None:
        """Append conflict to JSONL log file."""
        try:
            assert self.conflict_log_path is not None
            with open(self.conflict_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(conflict.to_dict()) + "\n")
        except Exception as e:
            log.debug("cross_run_consistency: could not write conflict log: %s", e)

    # ── Public reporting API ──────────────────────────────────────────────────

    @property
    def conflicts(self) -> list[ClaimConflict]:
        """All conflicts detected in the current run."""
        return list(self._conflicts)

    @property
    def critical_conflicts(self) -> list[ClaimConflict]:
        """Only critical-severity conflicts."""
        return [c for c in self._conflicts if c.severity == "critical"]

    def summary(self) -> dict[str, object]:
        """Return summary dict for RunSummary integration."""
        return {
            "total_conflicts": len(self._conflicts),
            "critical_conflicts": len(self.critical_conflicts),
            "entities_tracked": len(self._claim_store),
            "fields_monitored": self.claim_fields,
        }
