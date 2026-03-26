"""
veridian.verify.base
────────────────────
BaseVerifier ABC and VerificationResult.

RULES FOR ALL VERIFIERS:
1. NEVER call an LLM (except LLMJudgeVerifier, which is last-resort only).
2. Must be stateless — all config via __init__ or verify() params.
3. Must complete in < verification_timeout_seconds.
4. Must be idempotent — safe to call multiple times with same args.
5. Error messages must be:
   - SPECIFIC: say exactly what failed, not "verification failed"
   - ACTIONABLE: tell the agent what to fix and how
   - CONCISE: < 300 chars — this goes directly into the LLM context window
"""

from __future__ import annotations

import importlib.metadata
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from veridian.core.exceptions import VerifierNotFound
from veridian.core.task import Task, TaskResult

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """
    Returned by every verifier.
    If passed=False, error is injected verbatim into the next agent prompt.
    """

    passed: bool
    error: str | None = None  # injected into LLM context on failure
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None  # 0.0–1.0; used by LLMJudgeVerifier


class BaseVerifier(ABC):
    """
    Abstract base for all verifiers.
    Subclasses must define class-level `id` and `description`.
    """

    id: ClassVar[str]
    description: ClassVar[str] = ""

    @abstractmethod
    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run verification. Must be deterministic and idempotent."""
        ...

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Enforce that every concrete subclass declares an id
        if not getattr(cls, "id", None) and not getattr(cls, "__abstractmethods__", None):
            raise TypeError(f"{cls.__name__} must define a class-level 'id' string")


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────────────────────────────────────


class VerifierRegistry:
    """
    Global registry of verifier classes.

    Auto-discovery via Python entry points:
        [project.entry-points."veridian.verifiers"]
        my_verifier = "my_package.verifiers:MyVerifier"

    Third-party packages are auto-discovered on first registry access.
    """

    def __init__(self) -> None:
        self._classes: dict[str, type[BaseVerifier]] = {}
        self._discovered = False

    def register(self, cls: type[BaseVerifier]) -> None:
        """Register a verifier class. Raises if id already registered."""
        if not issubclass(cls, BaseVerifier):
            raise TypeError(f"{cls} is not a BaseVerifier subclass")
        if cls.id in self._classes:
            log.debug("verifier.register override id=%s", cls.id)
        self._classes[cls.id] = cls
        log.debug("verifier.register id=%s class=%s", cls.id, cls.__name__)

    def register_many(self, *classes: type[BaseVerifier]) -> None:
        for cls in classes:
            self.register(cls)

    def get(self, verifier_id: str, config: dict[str, Any] | None = None) -> BaseVerifier:
        """
        Instantiate and return a verifier by ID.
        Raises VerifierNotFound if not registered.
        """
        self._autodiscover()
        cls = self._classes.get(verifier_id)
        if cls is None:
            available = sorted(self._classes.keys())
            raise VerifierNotFound(
                f"Verifier {verifier_id!r} not found. "
                f"Available: {available}. "
                f"Register with verifier_registry.register(MyVerifier)."
            )
        if config:
            return cls(**config)
        return cls()

    def list_available(self) -> list[dict[str, str]]:
        """Return [{id, description}] for all registered verifiers."""
        self._autodiscover()
        return [
            {"id": vid, "description": cls.description}
            for vid, cls in sorted(self._classes.items())
        ]

    def _autodiscover(self) -> None:
        """Load entry-point plugins. Called once on first access."""
        if self._discovered:
            return
        self._discovered = True
        try:
            eps = importlib.metadata.entry_points(group="veridian.verifiers")
            for ep in eps:
                try:
                    cls = ep.load()
                    self.register(cls)
                    log.info("verifier.autodiscover id=%s from=%s", cls.id, ep.value)
                except Exception as e:
                    log.warning("verifier.autodiscover failed ep=%s err=%s", ep.name, e)
        except Exception as e:
            log.debug("verifier.autodiscover eps failed: %s", e)


# Module-level singleton — import this everywhere
registry = VerifierRegistry()
