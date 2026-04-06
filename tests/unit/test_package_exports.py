"""Package export hygiene tests for public subpackages."""

from veridian.agents.prompts import PROMPTS_DIR, WORKER_PROMPT_FILE
from veridian.ledger import SCHEMA_VERSION, TaskLedger
from veridian.providers import (
    LiteLLMProvider,
    LLMProvider,
    LLMResponse,
    Message,
    MockProvider,
)
from veridian.verify import (
    BaseVerifier,
    IntegrityResult,
    PipelineConfig,
    PRMVerifier,
    VerificationPipeline,
    VerificationResult,
    VerifierIntegrityChecker,
    VerifierRegistry,
    registry,
    verifier_registry,
)


def test_prompts_package_exports_prompt_paths() -> None:
    assert PROMPTS_DIR.is_dir()
    assert WORKER_PROMPT_FILE.exists()
    assert WORKER_PROMPT_FILE.name == "worker.md"


def test_ledger_package_exports_public_surface() -> None:
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1
    assert TaskLedger.__name__ == "TaskLedger"


def test_providers_package_exports_builtin_types() -> None:
    assert LLMProvider.__name__ == "LLMProvider"
    assert LLMResponse.__name__ == "LLMResponse"
    assert Message.__name__ == "Message"
    assert LiteLLMProvider.__name__ == "LiteLLMProvider"
    assert MockProvider.__name__ == "MockProvider"


def test_verify_package_exports_registry_and_pipeline() -> None:
    assert BaseVerifier.__name__ == "BaseVerifier"
    assert PRMVerifier.__name__ == "PRMVerifier"
    assert VerificationResult.__name__ == "VerificationResult"
    assert VerifierRegistry.__name__ == "VerifierRegistry"
    assert verifier_registry is registry
    assert IntegrityResult.__name__ == "IntegrityResult"
    assert VerifierIntegrityChecker.__name__ == "VerifierIntegrityChecker"
    assert PipelineConfig.__name__ == "PipelineConfig"
    assert VerificationPipeline.__name__ == "VerificationPipeline"
