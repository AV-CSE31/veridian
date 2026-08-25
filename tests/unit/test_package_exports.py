"""Package export hygiene tests for public subpackages."""

from veridian.core import VerificationReport
from veridian.core.contract import VerificationContract, VerifierStep, verify_completion
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
    VerificationResult,
    VerifierRegistry,
    registry,
    verifier_registry,
)


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


def test_verify_package_exports_registry_primitives() -> None:
    assert BaseVerifier.__name__ == "BaseVerifier"
    assert VerificationResult.__name__ == "VerificationResult"
    assert VerifierRegistry.__name__ == "VerifierRegistry"
    assert verifier_registry is registry


def test_core_package_exports_report_schema_by_module_path() -> None:
    assert VerificationReport.__name__ == "VerificationReport"


def test_core_package_exports_completion_contract_primitives() -> None:
    assert VerificationContract.__name__ == "VerificationContract"
    assert VerifierStep.__name__ == "VerifierStep"
    assert callable(verify_completion)
