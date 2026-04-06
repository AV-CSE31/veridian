"""
veridian.testing.replayer
──────────────────────────
Replayer — runs test assertions against recorded agent executions.

The replayer loads a trace file written by ``AgentRecorder``, then evaluates
each ``ReplayAssertion`` against every recorded run.  This is the
"pytest-for-agents" pattern: record once, assert many times.

Usage::

    from veridian.testing.replayer import Replayer, ReplayAssertion
    from veridian.testing.recorder import AgentRecorder

    recorder = AgentRecorder(trace_dir=Path("traces"))
    replayer = Replayer(recorder=recorder)
    replayer.add_assertion(
        ReplayAssertion(
            name="always_passes_verification",
            check=lambda rec: rec.verification_passed,
        )
    )
    results = replayer.run()
    for r in results:
        print(f"{r.assertion_name}[{r.run_id}]: {'PASS' if r.passed else 'FAIL'}")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from veridian.testing.recorder import AgentRecorder, RecordedRun

__all__ = ["ReplayAssertion", "ReplayResult", "Replayer"]


# ── ReplayAssertion ───────────────────────────────────────────────────────────


@dataclass
class ReplayAssertion:
    """
    A named test assertion evaluated against a ``RecordedRun``.

    Parameters
    ----------
    name:
        Human-readable assertion name (used in reports).
    check:
        Callable that accepts a ``RecordedRun`` and returns ``bool``.
    """

    name: str
    check: Callable[[RecordedRun], bool]

    def evaluate(self, run: RecordedRun) -> bool:
        """Evaluate this assertion against a single recorded run."""
        return self.check(run)


# ── ReplayResult ──────────────────────────────────────────────────────────────


@dataclass
class ReplayResult:
    """Result of evaluating one assertion against one recorded run."""

    assertion_name: str
    run_id: str
    passed: bool
    error: str | None = None  # populated if check() raised an exception


# ── Replayer ──────────────────────────────────────────────────────────────────


class Replayer:
    """
    Evaluates a suite of assertions against all recorded runs.

    Usage::

        replayer = Replayer(recorder=AgentRecorder(trace_dir=Path("traces")))
        replayer.add_assertion(ReplayAssertion("passes", lambda r: r.verification_passed))
        results = replayer.run()
        assert all(r.passed for r in results), "Replay assertions failed"
    """

    def __init__(self, recorder: AgentRecorder) -> None:
        """Initialize with the recorder that holds the trace file."""
        self._recorder = recorder
        self._assertions: list[ReplayAssertion] = []

    def add_assertion(self, assertion: ReplayAssertion) -> None:
        """Register an assertion to be evaluated on replay."""
        self._assertions.append(assertion)

    def run(
        self,
        runs: list[RecordedRun] | None = None,
    ) -> list[ReplayResult]:
        """
        Evaluate all assertions against all recorded runs.

        Parameters
        ----------
        runs:
            Override the loaded runs (useful for testing).  If ``None``,
            loads from the recorder's trace file.

        Returns
        -------
        list[ReplayResult]
            One entry per (assertion, run) combination.
        """
        recorded = runs if runs is not None else self._recorder.load()
        results: list[ReplayResult] = []

        for assertion in self._assertions:
            for rec in recorded:
                try:
                    passed = assertion.check(rec)
                    results.append(
                        ReplayResult(
                            assertion_name=assertion.name,
                            run_id=rec.run_id,
                            passed=passed,
                        )
                    )
                except Exception as exc:
                    results.append(
                        ReplayResult(
                            assertion_name=assertion.name,
                            run_id=rec.run_id,
                            passed=False,
                            error=str(exc),
                        )
                    )

        return results

    def summary(self, results: list[ReplayResult]) -> str:
        """Return a human-readable summary of replay results."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        lines = [f"Replay: {passed}/{total} passed, {failed} failed"]
        for r in results:
            status = "PASS" if r.passed else f"FAIL{': ' + r.error if r.error else ''}"
            lines.append(f"  [{status}] {r.assertion_name} (run={r.run_id})")
        return "\n".join(lines)
