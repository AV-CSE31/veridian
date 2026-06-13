"""Base verifier protocol, result type, and registry."""

from __future__ import annotations

import importlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from veridian.core.exceptions import VeridianConfigError, VeridianError, VerifierNotFound
from veridian.core.task import Task, TaskResult

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Returned by every verifier."""

    passed: bool
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    confidence_lower: float | None = None
    confidence_upper: float | None = None
    confidence_level: float | None = None
    verification_ms: float | None = None


class BaseVerifier(ABC):
    """Abstract base for deterministic verifiers."""

    id: ClassVar[str]
    description: ClassVar[str] = ""
    shareable: ClassVar[bool] = False

    @abstractmethod
    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Run verification. Must be deterministic and idempotent."""
        ...

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "id", None) and not getattr(cls, "__abstractmethods__", None):
            raise TypeError(f"{cls.__name__} must define a class-level 'id' string")


class VerifierRegistry:
    """Registry of verifier classes with lazy built-in and entry-point loading."""

    def __init__(self) -> None:
        self._classes: dict[str, type[BaseVerifier]] = {}
        self._lazy: dict[str, str] = {}
        self._discovered = False
        self._instance_cache: dict[tuple[str, str], BaseVerifier] = {}

    def register(self, cls: type[BaseVerifier]) -> None:
        """Register a verifier class. Re-registration replaces the old class."""
        if not issubclass(cls, BaseVerifier):
            raise TypeError(f"{cls} is not a BaseVerifier subclass")
        if cls.id in self._classes:
            log.debug("verifier.register override id=%s", cls.id)
        self._classes[cls.id] = cls
        for key in [k for k in self._instance_cache if k[0] == cls.id]:
            self._instance_cache.pop(key, None)
        log.debug("verifier.register id=%s class=%s", cls.id, cls.__name__)

    def register_many(self, *classes: type[BaseVerifier]) -> None:
        """Register several verifier classes."""
        for cls in classes:
            self.register(cls)

    def register_lazy(self, verifier_id: str, target: str) -> None:
        """Register ``module:Class`` without importing the module."""
        self._lazy[verifier_id] = target

    def register_lazy_many(self, targets: dict[str, str]) -> None:
        """Register several lazy verifier targets."""
        self._lazy.update(targets)

    def _load_lazy(self, verifier_id: str) -> None:
        target = self._lazy.get(verifier_id)
        if target is None or verifier_id in self._classes:
            return
        module_name, class_name = target.rsplit(":", 1)
        module = importlib.import_module(module_name)
        self.register(getattr(module, class_name))

    def _config_key(self, config: dict[str, Any] | None) -> str:
        """Return a stable cache key for verifier configuration."""
        if not config:
            return ""
        try:
            return json.dumps(config, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return "__unhashable__"

    def get(self, verifier_id: str, config: dict[str, Any] | None = None) -> BaseVerifier:
        """Instantiate and return a verifier by ID."""
        cls = self._resolve_class(verifier_id)

        if getattr(cls, "shareable", False):
            key = (verifier_id, self._config_key(config))
            if key[1] != "__unhashable__":
                cached = self._instance_cache.get(key)
                if cached is not None:
                    return cached
                instance = self._instantiate(cls, verifier_id, config)
                self._instance_cache[key] = instance
                return instance

        return self._instantiate(cls, verifier_id, config)

    def has(self, verifier_id: str) -> bool:
        """Return True when a verifier ID can be resolved without instantiating it."""
        try:
            self._resolve_class(verifier_id)
            return True
        except VerifierNotFound:
            return False

    def _resolve_class(self, verifier_id: str) -> type[BaseVerifier]:
        self._load_lazy(verifier_id)
        if verifier_id not in self._classes:
            self._autodiscover()
            self._load_lazy(verifier_id)

        cls = self._classes.get(verifier_id)
        if cls is None:
            available = sorted({*self._classes.keys(), *self._lazy.keys()})
            raise VerifierNotFound(
                f"Verifier {verifier_id!r} not found. "
                f"Available: {available}. "
                f"Register with verifier_registry.register(MyVerifier)."
            )
        return cls

    @staticmethod
    def _instantiate(
        cls: type[BaseVerifier],
        verifier_id: str,
        config: dict[str, Any] | None,
    ) -> BaseVerifier:
        try:
            return cls(**config) if config else cls()
        except VeridianError:
            raise
        except TypeError as exc:
            raise VeridianConfigError(
                f"Invalid verifier_config for verifier {verifier_id!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise VeridianConfigError(
                f"Invalid configuration for verifier {verifier_id!r}: {exc}"
            ) from exc

    def _autodiscover(self) -> None:
        """Load third-party verifier entry points once."""
        if self._discovered:
            return
        self._discovered = True
        try:
            import importlib.metadata

            eps = importlib.metadata.entry_points(group="veridian.verifiers")
            for ep in eps:
                try:
                    cls = ep.load()
                    self.register(cls)
                    log.info("verifier.autodiscover id=%s from=%s", cls.id, ep.value)
                except Exception as exc:
                    log.warning("verifier.autodiscover failed ep=%s err=%s", ep.name, exc)
        except Exception as exc:
            log.debug("verifier.autodiscover eps failed: %s", exc)


registry = VerifierRegistry()
registry.register_lazy_many(
    {
        "any_of": "veridian.verify.builtin.any_of:AnyOfVerifier",
        "bash_exit": "veridian.verify.builtin.bash:BashExitCodeVerifier",
        "composite": "veridian.verify.builtin.composite:CompositeVerifier",
        "file_exists": "veridian.verify.builtin.file_exists:FileExistsVerifier",
        "http_status": "veridian.verify.builtin.http:HttpStatusVerifier",
        "quote_match": "veridian.verify.builtin.quote:QuoteMatchVerifier",
        "schema": "veridian.verify.builtin.schema:SchemaVerifier",
    }
)
