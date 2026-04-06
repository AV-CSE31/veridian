"""
tests.unit.test_api_stability
──────────────────────────────
Phase D: Pin the stable public surface so additions/removals require an
explicit planning decision. v0.2 minimized surface (~40 symbols).

WCP-027: Extended with APISurfaceManifest tests for CI-enforced surface
tracking and deprecation automation.
"""

from __future__ import annotations

import json
from pathlib import Path

import veridian
from veridian.core.api_surface import APISurfaceManifest, SurfaceDiff, SymbolInfo

# The exact set of symbols in veridian.__all__ after v0.2 Phase D slimming.
_EXPECTED_STABLE_ALL = sorted(
    [
        "__version__",
        "Task",
        "TaskStatus",
        "TaskResult",
        "TaskPriority",
        "LedgerStats",
        "TaskLedger",
        "VeridianRunner",
        "VeridianConfig",
        "RunSummary",
        "ParallelRunner",
        "BaseVerifier",
        "VerificationResult",
        "verifier_registry",
        "BaseHook",
        "HookRegistry",
        "LLMProvider",
        "LLMResponse",
        "Message",
        "LiteLLMProvider",
        "MockProvider",
        "VeridianEvent",
        "RunStarted",
        "RunCompleted",
        "TaskClaimed",
        "TaskCompleted",
        "TaskFailed",
        "VeridianError",
        "VeridianConfigError",
        "InvalidTransition",
        "TaskNotFound",
        "TaskAlreadyClaimed",
        "VerificationError",
        "VerifierNotFound",
        "ProviderError",
        "HumanReviewRequired",
        "CostLimitExceeded",
        "BudgetExceeded",
        "Budget",
        "BudgetState",
    ]
)


class TestStableSurfaceIsExact:
    def test_all_matches_expected_set(self) -> None:
        """veridian.__all__ must be exactly the minimized stable surface.
        If you need to add or remove a symbol, update this list AND
        planning/API_STABILITY.md in the SAME PR."""
        actual = sorted(veridian.__all__)
        assert actual == _EXPECTED_STABLE_ALL, (
            f"veridian.__all__ drifted from the pinned surface.\n"
            f"Added:   {sorted(set(actual) - set(_EXPECTED_STABLE_ALL))}\n"
            f"Removed: {sorted(set(_EXPECTED_STABLE_ALL) - set(actual))}"
        )

    def test_stable_surface_count(self) -> None:
        assert len(veridian.__all__) <= 45, (
            f"veridian.__all__ has {len(veridian.__all__)} symbols; target is <=45 (was 123 in v3)"
        )


class TestExperimentalNamespace:
    def test_experimental_re_exports_adversarial_evaluator(self) -> None:
        from veridian.experimental import AdversarialEvaluator, EvaluationResult

        assert AdversarialEvaluator is not None
        assert EvaluationResult is not None

    def test_experimental_re_exports_sprint_contract(self) -> None:
        from veridian.experimental import (
            ContractRegistry,
            SprintContract,
            SprintContractHook,
            SprintContractVerifier,
        )

        for sym in (SprintContract, ContractRegistry, SprintContractVerifier, SprintContractHook):
            assert sym is not None

    def test_experimental_re_exports_record_replay(self) -> None:
        from veridian.experimental import (
            AgentRecorder,
            RecordedRun,
            ReplayAssertion,
            Replayer,
            ReplayResult,
        )

        for sym in (AgentRecorder, RecordedRun, ReplayAssertion, Replayer, ReplayResult):
            assert sym is not None

    def test_experimental_tier_marker_is_set(self) -> None:
        import veridian.experimental as exp

        assert exp.STABILITY_TIER == "experimental"
        assert len(exp.EXPERIMENTAL_SYMBOLS) >= 10


class TestStablePhaseBSurface:
    def test_activity_primitives_importable(self) -> None:
        from veridian.loop.activity import (
            ActivityError,
            ActivityJournal,
            ActivityRecord,
            RetryPolicy,
            run_activity,
        )

        for sym in (ActivityJournal, ActivityRecord, RetryPolicy, run_activity, ActivityError):
            assert sym is not None

    def test_replay_compat_primitives_importable(self) -> None:
        from veridian.loop.replay_compat import (
            ReplaySnapshot,
            build_run_replay_snapshot,
            check_replay_compatibility,
        )

        assert ReplaySnapshot is not None
        assert build_run_replay_snapshot is not None
        assert check_replay_compatibility is not None


class TestStablePhaseCSurface:
    def test_sdk_facade_functions_importable(self) -> None:
        from veridian.integrations.sdk import (
            ReplayReport,
            RunContext,
            VerificationOutcome,
            persist_state,
            record_step,
            replay_run,
            resume_run,
            start_run,
            verify_output,
        )

        for sym in (
            RunContext,
            VerificationOutcome,
            ReplayReport,
            start_run,
            record_step,
            verify_output,
            persist_state,
            resume_run,
            replay_run,
        ):
            assert sym is not None

    def test_langgraph_adapter_importable(self) -> None:
        from veridian.integrations.langgraph import (
            VeridianLangGraph,
            VerificationContract,
            VerificationError,
        )

        assert VeridianLangGraph is not None
        assert VerificationContract is not None
        assert VerificationError is not None

    def test_crewai_adapter_importable(self) -> None:
        from veridian.integrations.crewai import CrewVerificationContract, VeridianCrew

        assert VeridianCrew is not None
        assert CrewVerificationContract is not None

    def test_subgraph_primitives_importable(self) -> None:
        from veridian.integrations.subgraph import (
            SubgraphResult,
            complete_subgraph,
            start_subgraph,
        )

        assert start_subgraph is not None
        assert complete_subgraph is not None
        assert SubgraphResult is not None


class TestStablePhaseDSurface:
    def test_tenancy_primitives_importable(self) -> None:
        from veridian.integrations.tenancy import (
            TenantBudget,
            TenantBudgetExceeded,
            TenantIsolationError,
            TenantRateLimit,
            TenantRateLimitExceeded,
            TenantRegistry,
            TenantScope,
        )

        for sym in (
            TenantRegistry,
            TenantScope,
            TenantBudget,
            TenantRateLimit,
            TenantBudgetExceeded,
            TenantRateLimitExceeded,
            TenantIsolationError,
        ):
            assert sym is not None


class TestStablePhaseASurface:
    def test_control_flow_exceptions_importable(self) -> None:
        from veridian.core.exceptions import (
            ControlFlowSignal,
            HumanReviewRequired,
            TaskNotPaused,
            TaskPauseRequested,
            VeridianError,
        )

        exc = HumanReviewRequired(task_id="t", reason="x")
        assert isinstance(exc, VeridianError)
        assert isinstance(exc, ControlFlowSignal)
        assert not issubclass(TaskNotPaused, ControlFlowSignal)
        tpe = TaskPauseRequested(task_id="t", reason="x", payload={"a": 1})
        assert tpe.payload == {"a": 1}

    def test_paused_status_in_enum(self) -> None:
        from veridian.core.task import TaskStatus

        assert TaskStatus.PAUSED.value == "paused"
        assert TaskStatus.IN_PROGRESS.can_transition_to(TaskStatus.PAUSED)
        assert TaskStatus.PAUSED.can_transition_to(TaskStatus.IN_PROGRESS)

    def test_task_paused_events_importable(self) -> None:
        from veridian.core.events import TaskPaused, TaskResumed

        assert TaskPaused is not None
        assert TaskResumed is not None


class TestSdkVersionMetadata:
    def test_sdk_version_is_defined(self) -> None:
        from veridian.integrations import sdk

        assert hasattr(sdk, "SDK_VERSION")
        assert isinstance(sdk.SDK_VERSION, str)
        assert sdk.SDK_VERSION.count(".") >= 1


# ── WCP-027: APISurfaceManifest tests ───────────────────────────────────────


class TestAPISurfaceManifestCapture:
    """APISurfaceManifest.capture() must return all public symbols from veridian.__init__."""

    def test_capture_returns_all_public_symbols(self) -> None:
        manifest = APISurfaceManifest()
        captured = manifest.capture()
        expected = set(veridian.__all__)
        assert set(captured.keys()) == expected

    def test_capture_symbol_info_has_correct_fields(self) -> None:
        manifest = APISurfaceManifest()
        captured = manifest.capture()
        for name, info in captured.items():
            assert isinstance(info, SymbolInfo)
            assert info.name == name
            assert info.kind in ("class", "function", "constant")
            assert isinstance(info.module, str)
            assert isinstance(info.signature_hash, str)
            assert len(info.signature_hash) > 0


class TestAPISurfaceManifestDiffAdditions:
    """APISurfaceManifest.diff() must detect additions."""

    def test_diff_detects_additions(self) -> None:
        manifest = APISurfaceManifest()
        old: dict[str, SymbolInfo] = {}
        new = {
            "NewSymbol": SymbolInfo(
                name="NewSymbol", kind="class", module="veridian", signature_hash="abc123"
            ),
        }
        result = manifest.diff(old, new)
        assert isinstance(result, SurfaceDiff)
        assert "NewSymbol" in result.added
        assert result.removed == []
        assert result.changed == []


class TestAPISurfaceManifestDiffRemovals:
    """APISurfaceManifest.diff() must detect removals."""

    def test_diff_detects_removals(self) -> None:
        manifest = APISurfaceManifest()
        old = {
            "OldSymbol": SymbolInfo(
                name="OldSymbol", kind="function", module="veridian", signature_hash="def456"
            ),
        }
        new: dict[str, SymbolInfo] = {}
        result = manifest.diff(old, new)
        assert "OldSymbol" in result.removed
        assert result.added == []
        assert result.changed == []


class TestAPISurfaceManifestDiffSignatureChanges:
    """APISurfaceManifest.diff() must detect signature changes."""

    def test_diff_detects_signature_changes(self) -> None:
        manifest = APISurfaceManifest()
        sym = SymbolInfo(name="MyClass", kind="class", module="veridian", signature_hash="hash_v1")
        sym_changed = SymbolInfo(
            name="MyClass", kind="class", module="veridian", signature_hash="hash_v2"
        )
        old = {"MyClass": sym}
        new = {"MyClass": sym_changed}
        result = manifest.diff(old, new)
        assert "MyClass" in result.changed
        assert result.added == []
        assert result.removed == []


class TestAPISurfaceBaselineRoundtrip:
    """save_baseline / load_baseline must roundtrip through JSON."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        manifest = APISurfaceManifest()
        captured = manifest.capture()
        baseline_path = tmp_path / "api_surface_baseline.json"
        manifest.save_baseline(baseline_path, captured)
        loaded = manifest.load_baseline(baseline_path)
        assert set(loaded.keys()) == set(captured.keys())
        for name in captured:
            assert loaded[name].name == captured[name].name
            assert loaded[name].kind == captured[name].kind
            assert loaded[name].module == captured[name].module
            assert loaded[name].signature_hash == captured[name].signature_hash

    def test_saved_baseline_is_valid_json(self, tmp_path: Path) -> None:
        manifest = APISurfaceManifest()
        captured = manifest.capture()
        baseline_path = tmp_path / "api_surface_baseline.json"
        manifest.save_baseline(baseline_path, captured)
        data = json.loads(baseline_path.read_text())
        assert isinstance(data, dict)
        assert "symbols" in data
