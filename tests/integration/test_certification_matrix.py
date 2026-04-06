from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from veridian.integrations.certification import (
    CertificationResult,
    CertificationScenario,
    CertificationSuite,
    generate_matrix,
)


@dataclass
class _GoodAdapter:
    name: str = "good-adapter"
    version: str = "1.0.0"

    def connect(self) -> dict[str, Any]:
        return {"status": "connected"}

    def run_single_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "task": task}

    def handle_error(self, error: Exception) -> dict[str, Any]:
        return {"handled": True, "error": str(error)}

    def pause(self) -> dict[str, Any]:
        return {"paused": True}

    def resume(self) -> dict[str, Any]:
        return {"resumed": True}

    def checkpoint(self) -> dict[str, Any]:
        return {"checkpoint": "saved"}

    def restore(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return {"restored": True, "from": checkpoint}


@dataclass
class _PartialAdapter:
    name: str = "partial-adapter"
    version: str = "0.1.0"

    def connect(self) -> dict[str, Any]:
        return {"status": "connected"}

    def run_single_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


def test_certification_suite_default_scenarios_present() -> None:
    suite = CertificationSuite()
    names = {scenario.name for scenario in suite.scenarios}
    assert names == {
        "connect",
        "run_single_task",
        "handle_error",
        "pause_resume",
        "checkpoint_restore",
    }


def test_certification_run_success_for_good_adapter() -> None:
    suite = CertificationSuite()
    result = suite.run(_GoodAdapter())
    assert isinstance(result, CertificationResult)
    assert result.scenarios_passed == 5
    assert result.scenarios_failed == 0
    assert len(result.details) == 5


def test_certification_run_captures_missing_methods() -> None:
    suite = CertificationSuite()
    result = suite.run(_PartialAdapter())
    assert result.scenarios_passed >= 2
    assert result.scenarios_failed >= 1
    assert any("error" in detail for detail in result.details if not detail["passed"])


def test_certification_suite_accepts_custom_scenario() -> None:
    custom = CertificationScenario(
        name="custom",
        description="adapter has name attribute",
        test_fn=lambda adapter: bool(getattr(adapter, "name", "")),
    )
    suite = CertificationSuite(extra_scenarios=[custom])
    result = suite.run(_GoodAdapter())
    assert result.scenarios_passed == 6
    assert result.scenarios_failed == 0


def test_generate_matrix_payload_shape() -> None:
    matrix = generate_matrix([_GoodAdapter(), _PartialAdapter()])
    assert "generated_at" in matrix
    assert "suite_version" in matrix
    assert "results" in matrix
    assert len(matrix["results"]) == 2
    names = {row["adapter_name"] for row in matrix["results"]}
    assert names == {"good-adapter", "partial-adapter"}
