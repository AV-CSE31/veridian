"""
Minimal stubs for Veridian Phase 2+ components that have not yet been
implemented (they are on the roadmap for Phase 2–6).

These stubs provide just enough of the interface to run the experiment
suite. They are NOT production implementations — they demonstrate the
intended API surface and are replaced once the real modules ship.

Components stubbed here:
  - SchemaVerifier       (Phase 2) — validates required fields in structured output
  - BashExitCodeVerifier (Phase 2) — checks bash exit code
  - CompositeVerifier    (Phase 2) — AND-chain of verifiers
  - AnyOfVerifier        (Phase 2) — OR-chain of verifiers
  - LLMJudgeVerifier     (Phase 2) — LLM-based scoring (must be inside Composite)
  - BaseHook             (Phase 3) — base hook interface
  - HookRegistry         (Phase 3) — fire/register, errors always caught
  - EntropyGC            (Phase 6) — entropy/staleness detector (read-only)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Optional

from veridian.core.task import Task, TaskResult, TaskStatus
from veridian.core.exceptions import VeridianError
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 VERIFIER STUBS
# ─────────────────────────────────────────────────────────────────────────────


class SchemaVerifier(BaseVerifier):
    """Stub: validates required fields are present in structured output."""

    id: ClassVar[str] = "schema"
    description: ClassVar[str] = (
        "Validates that structured output contains required fields "
        "with optional type and value constraints. (Phase 2 stub)"
    )

    def __init__(
        self,
        required_fields: Optional[list[str]] = None,
        field_types: Optional[dict[str, type]] = None,
        allowed_values: Optional[dict[str, list]] = None,
        **_: Any,
    ) -> None:
        self.required_fields = required_fields or []
        self.field_types = field_types or {}
        self.allowed_values = allowed_values or {}

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        s = result.structured

        missing = [f for f in self.required_fields if f not in s]
        if missing:
            return VerificationResult(
                passed=False,
                error=f"[schema] Missing required fields: {missing}. "
                      f"Add them to the structured output.",
            )

        for fld, expected_type in self.field_types.items():
            if fld in s and not isinstance(s[fld], expected_type):
                return VerificationResult(
                    passed=False,
                    error=f"[schema] Field '{fld}' must be {expected_type.__name__} "
                          f"(got {type(s[fld]).__name__}). Fix the field type.",
                )

        for fld, allowed in self.allowed_values.items():
            if fld in s and s[fld] not in allowed:
                return VerificationResult(
                    passed=False,
                    error=f"[schema] Field '{fld}' must be one of {allowed} "
                          f"(got '{s[fld]}'). Fix the value.",
                )

        return VerificationResult(passed=True, evidence={"schema": "all fields present"})


class BashExitCodeVerifier(BaseVerifier):
    """Stub: checks that at least one bash command succeeded (exit_code == 0)."""

    id: ClassVar[str] = "bash_exit"
    description: ClassVar[str] = (
        "Verifies that bash commands completed with exit code 0. (Phase 2 stub)"
    )

    def __init__(self, command: Optional[str] = None, **_: Any) -> None:
        self.command = command

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        if not result.bash_outputs:
            return VerificationResult(
                passed=False,
                error="[bash_exit] No bash commands were executed. "
                      "Run at least one command to verify.",
            )
        # Check the last command (or the specific one if configured)
        last = result.bash_outputs[-1]
        if last.get("exit_code", 1) != 0:
            return VerificationResult(
                passed=False,
                error=f"[bash_exit] Command exited with code {last['exit_code']}. "
                      f"stderr: {last.get('stderr', '')[:100]}",
            )
        return VerificationResult(passed=True, evidence={"exit_code": 0})


class CompositeVerifier(BaseVerifier):
    """Stub: AND-chain of verifiers. All must pass."""

    id: ClassVar[str] = "composite"
    description: ClassVar[str] = (
        "Runs sub-verifiers in order; all must pass. (Phase 2 stub)"
    )

    def __init__(
        self,
        verifiers: Optional[list[BaseVerifier]] = None,
        **_: Any,
    ) -> None:
        self.verifiers: list[BaseVerifier] = verifiers or []
        # Guard: LLMJudgeVerifier cannot be the only verifier
        ids = [v.id for v in self.verifiers]
        if ids == ["llm_judge"]:
            from veridian.core.exceptions import VeridianError
            raise VeridianError(
                "LLMJudgeVerifier cannot run standalone. "
                "Wrap it with at least one deterministic verifier."
            )

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        for i, verifier in enumerate(self.verifiers, 1):
            vr = verifier.verify(task, result)
            if not vr.passed:
                n = len(self.verifiers)
                prefixed = (
                    f"[Step {i}/{n}] {verifier.id}: {vr.error or 'failed'}"
                )[:300]
                return VerificationResult(
                    passed=False,
                    error=prefixed,
                    evidence=vr.evidence,
                    score=vr.score,
                )
        return VerificationResult(
            passed=True,
            evidence={"composite": "all steps passed", "steps": len(self.verifiers)},
        )


class AnyOfVerifier(BaseVerifier):
    """Stub: OR-chain of verifiers. At least one must pass."""

    id: ClassVar[str] = "any_of"
    description: ClassVar[str] = (
        "Runs sub-verifiers in order; at least one must pass. (Phase 2 stub)"
    )

    def __init__(
        self,
        verifiers: Optional[list[BaseVerifier]] = None,
        **_: Any,
    ) -> None:
        self.verifiers: list[BaseVerifier] = verifiers or []

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        errors = []
        for verifier in self.verifiers:
            vr = verifier.verify(task, result)
            if vr.passed:
                return VerificationResult(
                    passed=True,
                    evidence={"any_of": f"passed via '{verifier.id}'"},
                )
            errors.append(f"{verifier.id}: {vr.error or 'failed'}")

        combined = "; ".join(errors)[:280]
        return VerificationResult(
            passed=False,
            error=f"[any_of] All verifiers failed: {combined}",
        )


class LLMJudgeVerifier(BaseVerifier):
    """Stub: LLM-based quality scoring. Must be inside CompositeVerifier."""

    id: ClassVar[str] = "llm_judge"
    description: ClassVar[str] = (
        "Scores output quality using an LLM judge. Always used inside Composite. "
        "(Phase 2 stub — uses litellm)"
    )

    def __init__(
        self,
        criteria: Optional[list[str]] = None,
        passing_score: float = 0.7,
        model: str = "gemini/gemini-2.0-flash",
        **_: Any,
    ) -> None:
        self.criteria = criteria or ["accuracy", "completeness"]
        self.passing_score = passing_score
        self.model = model

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        # Stub uses heuristic scoring rather than actual LLM to avoid cost
        text = result.raw_output.lower()
        score = 0.5
        if result.structured:
            score += 0.2
        if len(text) > 100:
            score += 0.15
        if any(c.lower() in text for c in self.criteria):
            score += 0.15
        score = round(min(1.0, score), 3)

        if score < self.passing_score:
            return VerificationResult(
                passed=False,
                score=score,
                error=(
                    f"[llm_judge] Quality score {score:.2f} below threshold "
                    f"{self.passing_score:.2f}. "
                    f"Improve: {', '.join(self.criteria[:2])}."
                ),
            )
        return VerificationResult(passed=True, score=score)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 HOOK STUBS
# ─────────────────────────────────────────────────────────────────────────────


class BaseHook:
    """Stub: base hook interface. All methods default to no-op."""

    id: ClassVar[str] = ""
    priority: ClassVar[int] = 50

    def on_run_started(self, event: Any) -> None:
        pass

    def before_task(self, event: Any) -> None:
        pass

    def after_result(self, event: Any) -> None:
        pass

    def on_task_completed(self, event: Any) -> None:
        pass

    def on_task_failed(self, event: Any) -> None:
        pass

    def on_run_completed(self, event: Any) -> None:
        pass


class HookRegistry:
    """Stub: fire/register with error isolation."""

    def __init__(self) -> None:
        self._hooks: list[BaseHook] = []

    def register(self, hook: Any) -> None:
        self._hooks.append(hook)

    def fire(self, event: Any) -> None:
        """Fire event to all hooks. Errors are caught and logged."""
        event_name = type(event).__name__.lower()
        method_map = {
            "runstarted": "on_run_started",
            "taskclaimed": "before_task",
            "taskcompleted": "on_task_completed",
            "taskfailed": "on_task_failed",
            "runcompleted": "on_run_completed",
        }
        method_name = method_map.get(event_name, "after_result")
        for hook in sorted(self._hooks, key=lambda h: getattr(h, "priority", 50)):
            try:
                method = getattr(hook, method_name, None)
                if method:
                    method(event)
            except Exception as exc:
                log.warning(
                    "hook_registry: hook '%s' raised %s — suppressed",
                    getattr(hook, "id", "?"),
                    exc,
                )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 ENTROPY GC STUB
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EntropyIssue:
    """Represents a single entropy issue detected by EntropyGC."""
    type: str
    task_id: str
    detail: str
    severity: str = "warning"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "detail": self.detail,
            "severity": self.severity,
        }


class EntropyGC:
    """
    Stub: read-only entropy/staleness detector.

    Checks a TaskLedger for common entropy patterns:
    1. Stale IN_PROGRESS tasks (stuck > stale_threshold_minutes)
    2. Tasks with too many retries (retry_count > max_retries_threshold)
    3. Abandoned dependency chains (tasks whose deps were abandoned)
    4. Orphaned tasks (depends_on references non-existent task IDs)

    NEVER mutates the ledger. Reports only.
    """

    def __init__(
        self,
        ledger: Any,
        stale_threshold_minutes: int = 60,
        max_retries_threshold: int = 3,
    ) -> None:
        self.ledger = ledger
        self.stale_threshold = timedelta(minutes=stale_threshold_minutes)
        self.max_retries_threshold = max_retries_threshold

    def check_stale_in_progress(self) -> list[EntropyIssue]:
        """Return IN_PROGRESS tasks stuck longer than stale_threshold."""
        now = datetime.utcnow()
        issues = []
        for task in self.ledger.list(status=TaskStatus.IN_PROGRESS):
            age = now - task.updated_at
            if age > self.stale_threshold:
                issues.append(EntropyIssue(
                    type="stale_in_progress",
                    task_id=task.id,
                    detail=(
                        f"Task IN_PROGRESS for {int(age.total_seconds() / 60)} min "
                        f"(threshold: {int(self.stale_threshold.total_seconds() / 60)} min)"
                    ),
                    severity="warning",
                ))
        return issues

    def check_excessive_retries(self) -> list[EntropyIssue]:
        """Return tasks that have exceeded the retry threshold."""
        issues = []
        for task in self.ledger.list():
            if task.retry_count >= self.max_retries_threshold:
                issues.append(EntropyIssue(
                    type="excessive_retries",
                    task_id=task.id,
                    detail=(
                        f"retry_count={task.retry_count} >= "
                        f"threshold={self.max_retries_threshold}"
                    ),
                    severity="critical" if task.retry_count >= self.max_retries_threshold * 2
                    else "warning",
                ))
        return issues

    def check_abandoned_dependency_chains(self) -> list[EntropyIssue]:
        """Return PENDING tasks whose dependencies have been ABANDONED."""
        issues = []
        all_tasks = {t.id: t for t in self.ledger.list()}
        for task in all_tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            for dep_id in task.depends_on:
                dep = all_tasks.get(dep_id)
                if dep and dep.status == TaskStatus.ABANDONED:
                    issues.append(EntropyIssue(
                        type="abandoned_dependency_chain",
                        task_id=task.id,
                        detail=(
                            f"Depends on abandoned task '{dep_id}'. "
                            "This task can never run."
                        ),
                        severity="critical",
                    ))
                    break
        return issues

    def check_orphaned_dependencies(self) -> list[EntropyIssue]:
        """Return tasks whose depends_on references non-existent task IDs."""
        issues = []
        all_ids = {t.id for t in self.ledger.list()}
        for task in self.ledger.list():
            for dep_id in task.depends_on:
                if dep_id not in all_ids:
                    issues.append(EntropyIssue(
                        type="orphaned_dependency",
                        task_id=task.id,
                        detail=f"depends_on references non-existent task '{dep_id}'",
                        severity="warning",
                    ))
        return issues

    def run(self, report_path: Optional[str] = None) -> list[EntropyIssue]:
        """Run all checks. Optionally write a markdown report. NEVER mutates ledger."""
        all_issues = (
            self.check_stale_in_progress()
            + self.check_excessive_retries()
            + self.check_abandoned_dependency_chains()
            + self.check_orphaned_dependencies()
        )

        if report_path:
            self._write_report(all_issues, report_path)

        return all_issues

    def _write_report(self, issues: list[EntropyIssue], path: str) -> None:
        """Write entropy_report.md. Read-only w.r.t. ledger."""
        import os
        import tempfile
        from pathlib import Path

        lines = [
            "# Entropy Report\n",
            f"Generated: {datetime.utcnow().isoformat()}Z\n",
            f"Total issues: {len(issues)}\n\n",
        ]
        by_type: dict[str, list[EntropyIssue]] = {}
        for issue in issues:
            by_type.setdefault(issue.type, []).append(issue)

        for issue_type, issue_list in sorted(by_type.items()):
            lines.append(f"## {issue_type.replace('_', ' ').title()} ({len(issue_list)})\n\n")
            for issue in issue_list:
                lines.append(f"- [{issue.severity.upper()}] `{issue.task_id}`: {issue.detail}\n")
            lines.append("\n")

        content = "".join(lines)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=out_path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        os.replace(tmp, str(out_path))
