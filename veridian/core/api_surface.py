"""
veridian.core.api_surface
──────────────────────────
CI-enforced API surface tracking and deprecation automation (WCP-027).

Captures the public API surface from ``veridian.__init__.__all__``, computes
diffs against a stored baseline, and enforces deprecation timelines so
removals cannot happen before the announced version.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "APISurfaceManifest",
    "DEPRECATION_REGISTRY",
    "SurfaceDiff",
    "SymbolInfo",
    "parse_version_tuple",
]


# ── Deprecation registry ────────────────────────────────────────────────────
# Every deprecated symbol MUST have an entry here with a removal_version.
# CI enforces that symbols are not removed from the experimental namespace
# before the announced version.

DEPRECATION_REGISTRY: dict[str, dict[str, str]] = {
    # Adversarial evaluation pipeline — deprecated in v0.2, removal in v0.4
    "AdversarialEvaluator": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "EvaluationResult": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "CalibrationProfile": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "GradingRubric": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "RubricCriterion": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "PipelineResult": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "VerificationPipeline": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    # Sprint Contract Protocol — deprecated in v0.2, removal in v0.4
    "SprintContract": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "ContractRegistry": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "SprintContractVerifier": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "SprintContractHook": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    # Record/replay harness — deprecated in v0.2, removal in v0.4
    "AgentRecorder": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "RecordedRun": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "ReplayAssertion": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "ReplayResult": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "Replayer": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    # GitHub Action harness — deprecated in v0.2, removal in v0.4
    "ActionConfig": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "ActionResult": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
    "run_action": {"removal_version": "0.4.0", "migration": "veridian.experimental"},
}


def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parse a PEP 440 version string into a comparable tuple of ints.

    Strips any pre-release/post-release suffixes and splits on dots.
    E.g. ``"0.2.0"`` -> ``(0, 2, 0)``, ``"1.0.0rc1"`` -> ``(1, 0, 0)``.
    """
    # Strip common suffixes like rc1, a1, b1, .dev1, .post1
    clean = version_str.split("rc")[0].split("a")[0].split("b")[0]
    clean = clean.split(".dev")[0].split(".post")[0]
    return tuple(int(p) for p in clean.split(".") if p.isdigit())


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SymbolInfo:
    """Metadata about a single public API symbol."""

    name: str
    kind: str  # "class" | "function" | "constant"
    module: str
    signature_hash: str


@dataclass(frozen=True)
class SurfaceDiff:
    """Result of comparing two API surface snapshots."""

    added: list[str]
    removed: list[str]
    changed: list[str]

    @property
    def has_changes(self) -> bool:
        """Return True if any additions, removals, or changes were detected."""
        return bool(self.added or self.removed or self.changed)


# ── Manifest ─────────────────────────────────────────────────────────────────


class APISurfaceManifest:
    """Captures and compares the public API surface of the veridian package."""

    def capture(self) -> dict[str, SymbolInfo]:
        """Introspect ``veridian.__init__.__all__`` and build a manifest.

        Returns a dict mapping symbol name to its :class:`SymbolInfo`.
        """
        import veridian as _veridian  # noqa: PLC0415

        result: dict[str, SymbolInfo] = {}
        for name in _veridian.__all__:
            obj = getattr(_veridian, name, None)
            kind = self._classify(obj)
            module = getattr(obj, "__module__", "veridian") or "veridian"
            sig_hash = self._compute_signature_hash(obj)
            result[name] = SymbolInfo(
                name=name,
                kind=kind,
                module=module,
                signature_hash=sig_hash,
            )
        return result

    def diff(
        self,
        old: dict[str, SymbolInfo],
        new: dict[str, SymbolInfo],
    ) -> SurfaceDiff:
        """Compute additions, removals, and signature changes between snapshots."""
        old_keys = set(old.keys())
        new_keys = set(new.keys())

        added = sorted(new_keys - old_keys)
        removed = sorted(old_keys - new_keys)
        changed: list[str] = []

        for name in sorted(old_keys & new_keys):
            if old[name].signature_hash != new[name].signature_hash:
                changed.append(name)

        return SurfaceDiff(added=added, removed=removed, changed=changed)

    def save_baseline(
        self,
        path: Path,
        surface: dict[str, SymbolInfo],
    ) -> None:
        """Persist the surface manifest to JSON using atomic write.

        Uses ``tempfile`` + ``os.replace()`` per CLAUDE.md prime directive #3.
        """
        data: dict[str, Any] = {
            "symbols": {name: asdict(info) for name, info in sorted(surface.items())},
        }
        content = json.dumps(data, indent=2, sort_keys=True) + "\n"
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(path))
        except BaseException:
            # Clean up temp file on failure
            with _suppress_os_error():
                os.unlink(tmp_path)
            raise

    def load_baseline(self, path: Path) -> dict[str, SymbolInfo]:
        """Load a previously saved baseline from JSON."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, SymbolInfo] = {}
        for name, info_dict in raw["symbols"].items():
            result[name] = SymbolInfo(
                name=info_dict["name"],
                kind=info_dict["kind"],
                module=info_dict["module"],
                signature_hash=info_dict["signature_hash"],
            )
        return result

    @staticmethod
    def _classify(obj: object) -> str:
        """Classify a symbol as 'class', 'function', or 'constant'."""
        if inspect.isclass(obj):
            return "class"
        if inspect.isfunction(obj) or inspect.isbuiltin(obj) or callable(obj):
            return "function"
        return "constant"

    @staticmethod
    def _compute_signature_hash(obj: object) -> str:
        """Hash of a callable's signature or a type's name.

        For classes/functions with inspectable signatures, the hash includes
        the full parameter list. For non-callable constants (strings, etc.),
        the hash is based on the ``repr``.
        """
        hasher = hashlib.sha256()
        if inspect.isclass(obj):
            # Include class name + __init__ signature if available
            hasher.update(f"class:{getattr(obj, '__qualname__', str(obj))}".encode())
            try:
                sig = inspect.signature(obj)
                hasher.update(str(sig).encode())
            except (ValueError, TypeError):
                pass
        elif callable(obj):
            hasher.update(f"func:{getattr(obj, '__qualname__', str(obj))}".encode())
            try:
                sig = inspect.signature(obj)
                hasher.update(str(sig).encode())
            except (ValueError, TypeError):
                pass
        else:
            hasher.update(f"const:{repr(obj)}".encode())
        return hasher.hexdigest()[:16]


# ── Helpers ──────────────────────────────────────────────────────────────────


class _suppress_os_error:
    """Context manager that suppresses OSError (for cleanup)."""

    def __enter__(self) -> None:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        return isinstance(exc_val, OSError)
