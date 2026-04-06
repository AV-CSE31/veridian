"""
veridian.integrations.certification
-----------------------------------
Adapter certification matrix primitives for WCP-019.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from veridian.core.exceptions import VeridianError

__all__ = [
    "CertificationError",
    "CertificationScenario",
    "CertificationResult",
    "CertificationSuite",
    "generate_matrix",
]

CERTIFICATION_VERSION = "1.0"


class CertificationError(VeridianError):
    """Fatal certification error."""

    def __init__(self, adapter_name: str, reason: str) -> None:
        self.adapter_name = adapter_name
        self.reason = reason
        super().__init__(f"Certification failed for {adapter_name!r}: {reason}")


@dataclass
class CertificationScenario:
    """One certification scenario."""

    name: str
    description: str
    test_fn: Callable[[Any], Any]


@dataclass
class CertificationResult:
    """Certification run output for one adapter/version."""

    adapter_name: str
    adapter_version: str
    scenarios_passed: int
    scenarios_failed: int
    details: list[dict[str, Any]] = field(default_factory=list)


def _scenario_connect(adapter: Any) -> bool:
    result = adapter.connect()
    return isinstance(result, dict)


def _scenario_run_single_task(adapter: Any) -> bool:
    result = adapter.run_single_task({"id": "cert-task-1"})
    return isinstance(result, dict)


def _scenario_handle_error(adapter: Any) -> bool:
    result = adapter.handle_error(RuntimeError("simulated failure"))
    return isinstance(result, dict)


def _scenario_pause_resume(adapter: Any) -> bool:
    paused = adapter.pause()
    resumed = adapter.resume()
    return isinstance(paused, dict) and isinstance(resumed, dict)


def _scenario_checkpoint_restore(adapter: Any) -> bool:
    checkpoint = adapter.checkpoint()
    restored = adapter.restore(checkpoint)
    return isinstance(checkpoint, dict) and isinstance(restored, dict)


_STANDARD_SCENARIOS: list[CertificationScenario] = [
    CertificationScenario(
        name="connect",
        description="Adapter can establish connectivity and report status",
        test_fn=_scenario_connect,
    ),
    CertificationScenario(
        name="run_single_task",
        description="Adapter can execute one task",
        test_fn=_scenario_run_single_task,
    ),
    CertificationScenario(
        name="handle_error",
        description="Adapter can handle a framework error without crashing",
        test_fn=_scenario_handle_error,
    ),
    CertificationScenario(
        name="pause_resume",
        description="Adapter supports pause/resume semantics",
        test_fn=_scenario_pause_resume,
    ),
    CertificationScenario(
        name="checkpoint_restore",
        description="Adapter supports checkpoint/restore semantics",
        test_fn=_scenario_checkpoint_restore,
    ),
]


class CertificationSuite:
    """Runs certification scenarios against adapter implementations."""

    def __init__(self, *, extra_scenarios: list[CertificationScenario] | None = None) -> None:
        self._scenarios = list(_STANDARD_SCENARIOS)
        if extra_scenarios:
            self._scenarios.extend(extra_scenarios)

    @property
    def scenarios(self) -> list[CertificationScenario]:
        return list(self._scenarios)

    def run(self, adapter: Any) -> CertificationResult:
        adapter_name = getattr(adapter, "name", type(adapter).__name__)
        adapter_version = getattr(adapter, "version", "unknown")
        passed = 0
        failed = 0
        details: list[dict[str, Any]] = []

        for scenario in self._scenarios:
            detail: dict[str, Any] = {"scenario": scenario.name, "passed": False}
            try:
                outcome = scenario.test_fn(adapter)
                if bool(outcome):
                    detail["passed"] = True
                    passed += 1
                else:
                    detail["error"] = "Scenario returned falsy result"
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                detail["error"] = f"{type(exc).__name__}: {exc}"
                failed += 1
            details.append(detail)

        return CertificationResult(
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            scenarios_passed=passed,
            scenarios_failed=failed,
            details=details,
        )


def generate_matrix(
    adapters: list[Any],
    suite: CertificationSuite | None = None,
) -> dict[str, Any]:
    """Run suite against adapters and emit a framework/version matrix payload."""
    active_suite = suite or CertificationSuite()
    results: list[dict[str, Any]] = []
    for adapter in adapters:
        cert = active_suite.run(adapter)
        results.append(
            {
                "adapter_name": cert.adapter_name,
                "adapter_version": cert.adapter_version,
                "scenarios_passed": cert.scenarios_passed,
                "scenarios_failed": cert.scenarios_failed,
                "details": cert.details,
            }
        )

    return {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(),
        "suite_version": CERTIFICATION_VERSION,
        "results": results,
    }
