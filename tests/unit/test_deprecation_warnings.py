"""
tests.unit.test_deprecation_warnings
─────────────────────────────────────
Phase B from 09-foundation-cleanup-release-plan: v0.2 hard-failure import tests.

In v0.2, deprecated experimental symbols are REMOVED from the top-level
``veridian.*`` namespace. Accessing them raises ``AttributeError`` with a
migration message pointing to ``veridian.experimental.*``.

Stable core symbols remain importable from ``veridian.*`` without any
warning or error.
"""

from __future__ import annotations

import importlib
from contextlib import suppress

import pytest

import veridian

# Symbols removed from veridian.* in v0.2 — must raise AttributeError.
_REMOVED_SYMBOLS = [
    "AdversarialEvaluator",
    "EvaluationResult",
    "CalibrationProfile",
    "GradingRubric",
    "RubricCriterion",
    "PipelineResult",
    "VerificationPipeline",
    "SprintContract",
    "ContractRegistry",
    "SprintContractVerifier",
    "SprintContractHook",
    "AgentRecorder",
    "RecordedRun",
    "ReplayAssertion",
    "ReplayResult",
    "Replayer",
    "ActionConfig",
    "ActionResult",
    "run_action",
]


def _clean_access(symbol: str):
    """Remove any cached module attribute so __getattr__ runs fresh."""
    with suppress(AttributeError):
        delattr(veridian, symbol)


class TestRemovedSymbolsRaiseAttributeError:
    @pytest.mark.parametrize("symbol", _REMOVED_SYMBOLS)
    def test_top_level_access_raises(self, symbol: str) -> None:
        _clean_access(symbol)
        with pytest.raises(AttributeError, match=r"veridian\.experimental"):
            getattr(veridian, symbol)

    @pytest.mark.parametrize("symbol", _REMOVED_SYMBOLS)
    def test_error_message_includes_migration_hint(self, symbol: str) -> None:
        _clean_access(symbol)
        with pytest.raises(AttributeError) as exc_info:
            getattr(veridian, symbol)
        msg = str(exc_info.value)
        assert symbol in msg
        assert "veridian.experimental" in msg
        assert "v0.2" in msg


class TestExperimentalNamespaceStillWorks:
    @pytest.mark.parametrize("symbol", _REMOVED_SYMBOLS)
    def test_experimental_import_succeeds(self, symbol: str) -> None:
        exp = importlib.import_module("veridian.experimental")
        value = getattr(exp, symbol, None)
        assert value is not None, (
            f"veridian.experimental.{symbol} must remain importable after removal from veridian.*"
        )


class TestStableCoreUnaffected:
    @pytest.mark.parametrize(
        "symbol",
        [
            "VeridianRunner",
            "VeridianConfig",
            "RunSummary",
            "ParallelRunner",
            "BaseHook",
            "HookRegistry",
            "LoggingHook",
            "HumanReviewHook",
            "Task",
            "TaskResult",
            "TaskStatus",
            "TaskLedger",
            "BaseVerifier",
            "VerificationResult",
            "LLMProvider",
            "MockProvider",
        ],
    )
    def test_stable_symbol_access_succeeds(self, symbol: str) -> None:
        # Do NOT clean_access for stable symbols — some are eagerly imported
        # at module load time and cannot be recovered via __getattr__.
        value = getattr(veridian, symbol, None)
        assert value is not None, f"Stable symbol veridian.{symbol} must remain accessible in v0.2"


# ── WCP-027: Deprecation metadata enforcement ──────────────────────────────


class TestDeprecatedSymbolsHaveRemovalVersion:
    """Deprecated symbols MUST carry removal_version metadata so the
    deprecation timeline is machine-readable and CI-enforceable."""

    def test_deprecated_symbols_have_removal_version(self) -> None:
        from veridian.core.api_surface import DEPRECATION_REGISTRY

        for symbol in _REMOVED_SYMBOLS:
            assert symbol in DEPRECATION_REGISTRY, (
                f"Deprecated symbol {symbol!r} not found in DEPRECATION_REGISTRY"
            )
            entry = DEPRECATION_REGISTRY[symbol]
            assert "removal_version" in entry, (
                f"Deprecated symbol {symbol!r} is missing 'removal_version' metadata"
            )
            assert isinstance(entry["removal_version"], str)
            assert entry["removal_version"], (
                f"Deprecated symbol {symbol!r} has empty removal_version"
            )


class TestDeprecationTimelineEnforcement:
    """Symbols cannot be removed before their announced removal_version."""

    def test_cannot_remove_before_announced_version(self) -> None:
        from veridian.core.api_surface import DEPRECATION_REGISTRY, parse_version_tuple

        current = parse_version_tuple(veridian.__version__)
        for symbol, entry in DEPRECATION_REGISTRY.items():
            removal = parse_version_tuple(entry["removal_version"])
            if current < removal:
                # Symbol should still exist in experimental namespace
                exp = importlib.import_module("veridian.experimental")
                value = getattr(exp, symbol, None)
                assert value is not None, (
                    f"Symbol {symbol!r} scheduled for removal in {entry['removal_version']} "
                    f"but already gone from veridian.experimental (current={veridian.__version__})"
                )
