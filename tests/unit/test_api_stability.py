"""Pin the slim top-level public API."""

from __future__ import annotations

import veridian

_EXPECTED_STABLE_ALL = sorted(
    [
        "__version__",
        "Task",
        "TaskLedger",
        "VeridianRunner",
        "VeridianConfig",
        "RunSummary",
        "BaseVerifier",
        "VerificationResult",
        "BaseHook",
        "HookRegistry",
        "LLMProvider",
        "LLMResponse",
        "Message",
        "MockProvider",
        "LiteLLMProvider",
        "VerifiedCall",
        "VeridianError",
        "VerificationError",
        "ProviderError",
        "VerificationContract",
        "VerificationDecision",
        "VerifierStep",
        "verify_completion",
        "verified",
    ]
)


class TestStableSurfaceIsExact:
    def test_all_matches_expected_set(self) -> None:
        actual = sorted(veridian.__all__)
        assert actual == _EXPECTED_STABLE_ALL, (
            f"veridian.__all__ drifted from the pinned slim surface.\n"
            f"Added:   {sorted(set(actual) - set(_EXPECTED_STABLE_ALL))}\n"
            f"Removed: {sorted(set(_EXPECTED_STABLE_ALL) - set(actual))}"
        )

    def test_stable_surface_count(self) -> None:
        assert len(veridian.__all__) <= 24

    def test_removed_symbols_are_module_path_only(self) -> None:
        removed = [
            "TaskStatus",
            "TaskResult",
            "TaskPriority",
            "LedgerStats",
            "ParallelRunner",
            "verifier_registry",
            "VeridianEvent",
            "RunStarted",
            "RunCompleted",
            "TaskClaimed",
            "TaskCompleted",
            "TaskFailed",
            "Budget",
            "BudgetState",
        ]
        for symbol in removed:
            assert symbol not in veridian.__all__
