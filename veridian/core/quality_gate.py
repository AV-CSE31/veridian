"""
veridian.core.quality_gate
───────────────────────────
TaskQualityGate — Gap 4 implementation.

RESEARCH BASIS:
  METR messiness factor analysis (2025):
    Tasks were labelled on 16 "messiness" factors. Performance on messy tasks
    is 40–60% lower than on clean tasks with the same human time estimate.
    Root causes: lack of explicit success criteria, implicit dependencies,
    tasks that are not truly atomic.

  SWE-Bench PRO human-verification loop (arXiv 2509.16941):
    "We design a novel three-stage human-in-the-loop process: clarifying
     ambiguity and adding missing context, and recovering unit tests as
     robust verifiers by constraining solution spaces to avoid false negatives."

  METR algorithmic vs holistic evaluation (Aug 2025):
    "Agents can implement the core functionality of tasks moderately well.
     However, to actually be mergeable, there are often many other important
     goals you need to satisfy, and this agent isn't able to satisfy all
     of them in a single trajectory."

PURPOSE:
  TaskQualityGate runs between InitializerAgent output and ledger.add().
  It scores each task on 5 quality axes and rejects (or flags for revision)
  tasks that score below threshold.

  This is the veridian equivalent of SWE-Bench PRO's human-verification loop —
  but automated and specific to verifiable task properties.

QUALITY AXES (each 0.0–1.0):
  1. specificity      — does the description say what "done" looks like?
  2. verifiability    — does the verifier_id match the implied output type?
  3. atomicity        — is the task a single verifiable unit, not a bundle?
  4. dep_soundness    — do depends_on IDs exist? no dependency cycles?
  5. context_complete — are required metadata fields present for the verifier?

USAGE:
  gate = TaskQualityGate(min_score=0.6, fail_on_below=0.4)

  # Standalone
  results = gate.evaluate(tasks, all_task_ids)
  approved, rejected = gate.split(results)

  # With revision loop (requires InitializerAgent)
  approved = gate.evaluate_with_revision(
      tasks=tasks,
      all_task_ids=all_task_ids,
      initializer=initializer_agent,
      max_revision_rounds=2,
  )

  ledger.add(approved)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from veridian.core.task import Task

log = logging.getLogger(__name__)


# ── Quality Score ─────────────────────────────────────────────────────────────

@dataclass
class QualityScore:
    """
    Quality assessment for a single task.
    """
    task_id: str
    task_title: str
    specificity: float = 0.0
    verifiability: float = 0.0
    atomicity: float = 0.0
    dep_soundness: float = 0.0
    context_complete: float = 0.0
    composite: float = 0.0
    passed: bool = False
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "scores": {
                "specificity": round(self.specificity, 2),
                "verifiability": round(self.verifiability, 2),
                "atomicity": round(self.atomicity, 2),
                "dep_soundness": round(self.dep_soundness, 2),
                "context_complete": round(self.context_complete, 2),
                "composite": round(self.composite, 2),
            },
            "passed": self.passed,
            "issues": self.issues,
        }


# ── Per-verifier metadata requirements ───────────────────────────────────────

_VERIFIER_METADATA_REQUIREMENTS: dict[str, list[str]] = {
    "bash_exit": [],                              # no special metadata needed
    "quote_match": ["source_file"],
    "schema": [],                                 # verifier_config.schema required
    "http_status": [],                            # url in verifier_config
    "file_exists": [],                            # paths in verifier_config
    "composite": [],
    "any_of": [],
    "llm_judge": [],
    "legal_clause": ["source_file"],
    "soc2_control": ["policy_corpus_dir"],
    "schema_output": ["pipeline_file", "expected_keys"],
    "py3_migration": ["source_file"],
}

# Verifier IDs that imply structured output requirements
_VERIFIER_STRUCTURED_FIELDS: dict[str, list[str]] = {
    "legal_clause": ["clause_type", "quote", "page_number", "risk_level"],
    "soc2_control": ["status", "evidence_source", "evidence_quote"],
    "content_moderation": ["decision", "reasoning"],
}


class TaskQualityGate:
    """
    Evaluates task quality on 5 axes before adding to ledger.
    Rejects tasks below fail_on_below, flags tasks between fail_on_below and min_score.
    """

    # Weight of each axis in composite score
    WEIGHTS = {
        "specificity": 0.30,
        "verifiability": 0.25,
        "atomicity": 0.20,
        "dep_soundness": 0.15,
        "context_complete": 0.10,
    }

    def __init__(
        self,
        min_score: float = 0.60,         # score below this → flagged for revision
        fail_on_below: float = 0.35,     # score below this → hard reject
        require_verifier_match: bool = True,
        require_success_criteria: bool = True,
        log_quality_report: bool = True,
    ) -> None:
        self.min_score = min_score
        self.fail_on_below = fail_on_below
        self.require_verifier_match = require_verifier_match
        self.require_success_criteria = require_success_criteria
        self.log_quality_report = log_quality_report

    def evaluate(
        self,
        tasks: list[Task],
        all_task_ids: set[str] | None = None,
    ) -> list[QualityScore]:
        """
        Evaluate all tasks. Returns list of QualityScore in same order.
        """
        if all_task_ids is None:
            all_task_ids = {t.id for t in tasks}

        scores = []
        for task in tasks:
            score = self._score_task(task, all_task_ids)
            score.passed = score.composite >= self.min_score
            scores.append(score)

        if self.log_quality_report:
            self._log_report(scores)

        return scores

    def split(
        self, scores: list[QualityScore]
    ) -> tuple[list[QualityScore], list[QualityScore]]:
        """Split into (approved, rejected) based on thresholds."""
        approved = [s for s in scores if s.composite >= self.min_score]
        rejected = [s for s in scores if s.composite < self.fail_on_below]
        return approved, rejected

    def filter_tasks(
        self,
        tasks: list[Task],
        all_task_ids: set[str] | None = None,
    ) -> tuple[list[Task], list[QualityScore]]:
        """
        Evaluate tasks and return (approved_tasks, all_scores).
        Convenience method for use in runner/initializer.
        """
        scores = self.evaluate(tasks, all_task_ids)
        score_by_id = {s.task_id: s for s in scores}
        approved = [t for t in tasks if score_by_id[t.id].composite >= self.min_score]
        return approved, scores

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score_task(self, task: Task, all_task_ids: set[str]) -> QualityScore:
        score = QualityScore(task_id=task.id, task_title=task.title)
        issues: list[str] = []

        score.specificity, spec_issues = self._score_specificity(task)
        issues.extend(spec_issues)

        score.verifiability, verif_issues = self._score_verifiability(task)
        issues.extend(verif_issues)

        score.atomicity, atom_issues = self._score_atomicity(task)
        issues.extend(atom_issues)

        score.dep_soundness, dep_issues = self._score_dep_soundness(task, all_task_ids)
        issues.extend(dep_issues)

        score.context_complete, ctx_issues = self._score_context_completeness(task)
        issues.extend(ctx_issues)

        # Composite weighted score
        score.composite = (
            score.specificity * self.WEIGHTS["specificity"] +
            score.verifiability * self.WEIGHTS["verifiability"] +
            score.atomicity * self.WEIGHTS["atomicity"] +
            score.dep_soundness * self.WEIGHTS["dep_soundness"] +
            score.context_complete * self.WEIGHTS["context_complete"]
        )
        score.issues = issues
        return score

    def _score_specificity(self, task: Task) -> tuple[float, list[str]]:
        """
        Does the description explicitly say what 'done' looks like?
        Checks for: success criteria keywords, minimum length, field requirements.
        """
        desc = task.description.lower()
        issues = []
        score = 1.0

        # Minimum length check
        if len(task.description.strip()) < 30:
            issues.append("Description too short (< 30 chars). Add what 'done' looks like.")
            return 0.1, issues

        # Checks for explicit success criteria
        success_indicators = [
            "must", "should", "verify", "ensure", "check", "confirm",
            "output", "result", "produce", "generate", "return",
            "pass", "succeed", "complete", "done when", "done if",
        ]
        has_success_criterion = any(kw in desc for kw in success_indicators)

        if self.require_success_criteria and not has_success_criterion:
            issues.append(
                "Description lacks explicit success criteria. "
                "Add: what does done look like? What output is expected?"
            )
            score -= 0.35

        # Vagueness penalties
        vague_phrases = [
            "do the thing", "handle it", "deal with", "take care of",
            "work on", "improve", "update as needed", "fix it",
        ]
        if any(phrase in desc for phrase in vague_phrases):
            issues.append(
                "Description contains vague language. Be specific about required actions."
            )
            score -= 0.20

        # Reward structured descriptions (bullets, numbered lists, examples)
        structure_indicators = ["\n", "1.", "2.", "- ", "* ", "e.g.", "for example"]
        if any(s in task.description for s in structure_indicators):
            score = min(1.0, score + 0.10)

        return max(0.0, score), issues

    def _score_verifiability(self, task: Task) -> tuple[float, list[str]]:
        """
        Does the verifier_id make sense for this task?
        Does verifier_config have required fields?
        """
        issues = []
        score = 1.0
        v_id = task.verifier_id
        v_cfg = task.verifier_config

        # Check verifier_id is set
        if v_id == "bash_exit" and not v_cfg.get("command"):
            # Default verifier with no command configured → needs command
            # Check if description mentions a test command
            desc = task.description.lower()
            if "pytest" in desc or "test" in desc:
                issues.append(
                    "verifier_id=bash_exit but no command in verifier_config. "
                    "Add: verifier_config={'command': 'pytest ...'}"
                )
                score -= 0.30

        # Verify schema verifier has a schema
        if v_id == "schema" and "schema" not in v_cfg:
            issues.append("verifier_id=schema but no 'schema' in verifier_config.")
            score -= 0.40

        # Verify http_status has a url
        if v_id == "http_status" and "url" not in v_cfg:
            issues.append("verifier_id=http_status but no 'url' in verifier_config.")
            score -= 0.40

        # Verify composite has verifiers list
        if v_id in ("composite", "any_of") and not v_cfg.get("verifiers"):
            issues.append(f"verifier_id={v_id} but no 'verifiers' list in verifier_config.")
            score -= 0.50

        # Check description aligns with verifier type
        if self.require_verifier_match:
            desc = task.description.lower()
            if v_id == "bash_exit" and not any(
                kw in desc for kw in
                ["run", "execute", "test", "pytest", "script", "command", "compile"]
            ):
                    issues.append(
                        "verifier=bash_exit but description doesn't mention running a command."
                    )
                    score -= 0.15

        return max(0.0, score), issues

    def _score_atomicity(self, task: Task) -> tuple[float, list[str]]:
        """
        Is this task a single verifiable unit?
        Flags tasks that describe multiple independent outcomes.
        """
        issues = []
        score = 1.0
        desc = task.description

        # Count action verbs that suggest multiple steps
        multi_step_patterns = [
            r"\band\b.*\band\b",          # X and Y and Z
            r"\d\.\s+.+\n\s*\d\.\s+",    # numbered list with 3+ items
            r"first.+then.+finally",       # first ... then ... finally
            r"step \d+.+step \d+",        # step 1 ... step 2
        ]
        multi_step = sum(
            1 for p in multi_step_patterns
            if re.search(p, desc.lower(), re.DOTALL)
        )

        if multi_step >= 2:
            issues.append(
                "Task description suggests multiple independent steps. "
                "Consider splitting into atomic tasks with depends_on."
            )
            score -= 0.25 * min(multi_step, 3)

        # Check for "and" connecting independent verifiable outcomes
        and_outcomes = len(re.findall(
            r"(create|generate|produce|output|verify|check|ensure).+and.+"
            r"(create|generate|produce|output|verify|check|ensure)",
            desc.lower()
        ))
        if and_outcomes >= 2:
            issues.append(
                "Task bundles multiple verifiable outcomes. "
                "Split into separate atomic tasks."
            )
            score -= 0.20

        return max(0.0, score), issues

    def _score_dep_soundness(
        self, task: Task, all_task_ids: set[str]
    ) -> tuple[float, list[str]]:
        """
        Do all depends_on IDs exist? No self-dependency?
        Cycle detection is done at the TaskGraph level — here we check references.
        """
        issues = []
        score = 1.0

        for dep_id in task.depends_on:
            if dep_id == task.id:
                issues.append(f"Task depends on itself: depends_on contains '{dep_id}'.")
                score -= 0.50
            elif dep_id not in all_task_ids:
                issues.append(
                    f"depends_on references unknown task '{dep_id}'. "
                    "Check task ID or remove the dependency."
                )
                score -= 0.30

        return max(0.0, score), issues

    def _score_context_completeness(self, task: Task) -> tuple[float, list[str]]:
        """
        Are required metadata fields present for the verifier?
        """
        issues = []
        score = 1.0
        v_id = task.verifier_id
        required_meta = _VERIFIER_METADATA_REQUIREMENTS.get(v_id, [])

        missing = [
            f for f in required_meta
            if f not in task.metadata and f not in task.verifier_config
        ]
        if missing:
            issues.append(
                f"verifier '{v_id}' requires metadata fields {missing}. "
                f"Add them to task.metadata."
            )
            score -= 0.20 * len(missing)

        return max(0.0, score), issues

    # ── Reporting ─────────────────────────────────────────────────────────────

    def _log_report(self, scores: list[QualityScore]) -> None:
        total = len(scores)
        passed = sum(1 for s in scores if s.passed)
        avg = sum(s.composite for s in scores) / total if total else 0

        log.info(
            "task_quality_gate: %d/%d tasks passed (avg score=%.2f)",
            passed, total, avg,
        )

        failed = [s for s in scores if not s.passed]
        for score in failed[:5]:  # log first 5 failures
            log.warning(
                "task_quality [%.2f] '%s': %s",
                score.composite,
                score.task_title[:50],
                "; ".join(score.issues[:2]),
            )
        if len(failed) > 5:
            log.warning("... and %d more failed tasks", len(failed) - 5)


# ── TaskGraph: dependency cycle detection ─────────────────────────────────────

class TaskGraph:
    """
    Builds a dependency graph from a list of tasks.
    Used to detect cycles before ledger.add().
    """

    @staticmethod
    def detect_cycles(tasks: list[Task]) -> list[list[str]]:
        """
        Returns list of cycles found (each cycle as list of task IDs).
        Empty list means no cycles.
        """
        graph: dict[str, list[str]] = {t.id: t.depends_on for t in tasks}
        cycles = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        parent: dict[str, str | None] = {}

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in graph.get(node, []):
                if neighbour not in visited:
                    parent[neighbour] = node
                    if dfs(neighbour):
                        return True
                elif neighbour in rec_stack:
                    # Found a cycle — reconstruct it
                    cycle = [neighbour]
                    curr: str | None = node
                    while curr != neighbour and curr is not None:
                        cycle.append(curr)
                        curr = parent.get(curr)
                    cycle.append(neighbour)
                    cycles.append(list(reversed(cycle)))
                    return True
            rec_stack.discard(node)
            return False

        for task_id in graph:
            if task_id not in visited:
                parent[task_id] = None
                dfs(task_id)

        return cycles

    @staticmethod
    def topological_sort(tasks: list[Task]) -> list[Task]:
        """
        Return tasks in dependency order (dependencies before dependents).
        Raises ValueError if cycles exist.
        """
        graph: dict[str, list[str]] = {t.id: t.depends_on for t in tasks}
        task_map = {t.id: t for t in tasks}
        in_degree: dict[str, int] = {t.id: 0 for t in tasks}

        for task_id, deps in graph.items():
            for dep in deps:
                if dep in task_map:
                    in_degree[task_id] += 1

        # Kahn's algorithm
        from collections import deque
        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        result = []

        while queue:
            tid = queue.popleft()
            result.append(task_map[tid])
            for t in tasks:
                if tid in t.depends_on:
                    in_degree[t.id] = in_degree.get(t.id, 1) - 1
                    if in_degree[t.id] == 0:
                        queue.append(t.id)

        if len(result) != len(tasks):
            raise ValueError(
                f"Dependency cycle detected: {len(tasks) - len(result)} tasks in cycle"
            )

        return result
