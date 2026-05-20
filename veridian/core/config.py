"""
veridian.core.config
---------------------------------------------------------------
VeridianConfig --- central configuration for the Veridian runner.
All fields have sensible defaults. Model is read from VERIDIAN_MODEL env var
if not set explicitly. The runtime must never hardcode model names.
"""

from __future__ import annotations

import os
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.core.exceptions import VeridianConfigError

__all__ = ["VeridianConfig", "default_data_dir"]


_TRUE_LITERALS = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_LITERALS = frozenset({"0", "false", "no", "off", "n", "f"})
_NONE_LITERALS = frozenset({"", "none", "null"})


def _unwrap_optional(hint: Any) -> Any:
    """If ``hint`` is ``T | None`` / ``Optional[T]`` return ``T`` else ``hint``."""
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return hint


def _coerce_env_value(raw: str, hint: Any, env_key: str) -> Any:
    """Coerce an environment-variable string to the dataclass field type.

    Handles ``int``, ``float``, ``bool``, ``str``, ``Path``, and the
    ``T | None`` shape used pervasively by :class:`VeridianConfig`.
    Unsupported types fall back to passing the raw string through ---
    the dataclass constructor will then raise its own TypeError, which
    is preferable to a silent miscoercion.
    """
    stripped = raw.strip()
    target = _unwrap_optional(hint)

    # Optional fields: empty/"none" clears the override.
    nullable = target is not hint
    if nullable and stripped.lower() in _NONE_LITERALS:
        return None

    if target is bool:
        lowered = stripped.lower()
        if lowered in _TRUE_LITERALS:
            return True
        if lowered in _FALSE_LITERALS:
            return False
        raise VeridianConfigError(
            f"Env var {env_key}={raw!r} is not a valid boolean "
            f"(expected one of {sorted(_TRUE_LITERALS | _FALSE_LITERALS)})"
        )
    if target is int:
        return int(stripped)
    if target is float:
        return float(stripped)
    if target is Path:
        return Path(stripped).expanduser()
    if target is str:
        return stripped
    # Unknown / complex type --- pass through. The dataclass will reject
    # mismatches at construction.
    return raw


_DEFAULT_MODEL = "gemini/gemini-2.5-flash"


def default_data_dir() -> Path:
    """Resolve the directory under which ledger/progress files live by default.

    Container-friendly resolution order:

    1. ``VERIDIAN_DATA_DIR`` env var, if set. Created if absent.
    2. The current working directory (backwards-compatible default for
       existing scripts and unit tests).

    Set ``VERIDIAN_DATA_DIR=/var/lib/veridian`` in production containers
    to persist state on a mounted volume rather than the ephemeral PWD.
    """
    override = os.getenv("VERIDIAN_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


@dataclass
class VeridianConfig:
    """
    Central configuration for VeridianRunner and supporting components.

    All model-selection logic MUST read from this config --- never hardcode
    model names anywhere else in the codebase.
    """

    # ------ LLM ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    model: str = field(default_factory=lambda: os.getenv("VERIDIAN_MODEL", _DEFAULT_MODEL))
    temperature: float = 0.2
    max_tokens: int = 4096
    provider_timeout: int = 120

    # ------ Runner ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    max_turns_per_task: int = 10  # WorkerAgent loop limit
    max_retries: int = 3  # per-task retry budget
    dry_run: bool = False  # assemble context only, no LLM calls

    # RV3-001: Resume PAUSED tasks before fetching new PENDING work. Keeps HITL
    # approvals from being starved by newly queued tasks.
    resume_paused_on_start: bool = True

    # RV3-003: Fail-closed when a replay snapshot (model/prompt/verifier config)
    # changes between runs for the same task.
    strict_replay: bool = True

    # ------ Storage ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # Defaults are anchored to default_data_dir() so containers can persist
    # state by setting ``VERIDIAN_DATA_DIR=/var/lib/veridian``. Bare paths
    # like ``ledger.json`` continue to resolve relative to that root.
    ledger_file: Path = field(default_factory=lambda: default_data_dir() / "ledger.json")
    progress_file: Path = field(default_factory=lambda: default_data_dir() / "progress.md")
    # FileLock acquire timeout for ledger writes. Tight enough that a
    # crashed peer with a stale lock surfaces a clear Timeout instead of
    # blocking pod startup indefinitely.
    ledger_lock_timeout: float = 15.0

    # ------ Context ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    context_window_tokens: int = 8000  # token budget for context assembly
    # ------ Concurrency ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # ------ Cost guard ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    max_cost_usd: float = 50.0

    # ------ Observability ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # ------ Phase filter ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    phase: str | None = None  # restrict run to this phase

    @classmethod
    def from_env(
        cls,
        prefix: str = "VERIDIAN_",
        env: dict[str, str] | None = None,
        **overrides: Any,
    ) -> VeridianConfig:
        """Construct a :class:`VeridianConfig` from environment variables.

        For every dataclass field ``foo_bar`` the helper reads
        ``${prefix}FOO_BAR`` (uppercase) from the environment and coerces
        the string to the field's declared type. Anything passed in
        ``**overrides`` wins over the env var, which in turn wins over
        the dataclass default.

        Type coercion handles ``int``, ``float``, ``bool``, ``Path``,
        and ``str``; ``bool`` accepts ``1/0/true/false/yes/no/on/off``
        case-insensitively. ``None``-valued env strings (``""`` or
        ``"none"``) clear optional fields.

        This makes ``VeridianConfig`` Kubernetes ConfigMap / 12-factor
        friendly: a Deployment manifest can set
        ``VERIDIAN_MAX_COST_USD=25.0`` and the runner
        picks them up without code changes.
        """
        import dataclasses
        import typing

        source = os.environ if env is None else env
        kwargs: dict[str, Any] = {}

        # Build {field_name: type_hint} so we can coerce env strings.
        type_hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            env_key = f"{prefix}{f.name.upper()}"
            if env_key not in source:
                continue
            raw = source[env_key]
            hint = type_hints.get(f.name, str)
            try:
                kwargs[f.name] = _coerce_env_value(raw, hint, env_key)
            except VeridianConfigError:
                raise
            except Exception as exc:
                raise VeridianConfigError(
                    f"Env var {env_key}={raw!r} could not be coerced to {hint}: {exc}"
                ) from exc

        kwargs.update(overrides)
        return cls(**kwargs)

    def __post_init__(self) -> None:
        """Validate fields that would otherwise produce confusing runtime
        errors much later. These checks catch operator typos at config
        construction rather than letting a zero cost-cap turn into an
        infinite-budget run.
        """
        from veridian.core.exceptions import VeridianConfigError  # local import

        def _require_positive(name: str, value: float) -> None:
            if value <= 0:
                raise VeridianConfigError(f"VeridianConfig.{name} must be > 0, got {value!r}")

        def _require_non_negative(name: str, value: float) -> None:
            if value < 0:
                raise VeridianConfigError(f"VeridianConfig.{name} must be >= 0, got {value!r}")

        _require_positive("max_turns_per_task", self.max_turns_per_task)
        _require_non_negative("max_retries", self.max_retries)
        _require_positive("provider_timeout", self.provider_timeout)
        _require_positive("max_tokens", self.max_tokens)
        _require_positive("context_window_tokens", self.context_window_tokens)
        _require_positive("max_cost_usd", self.max_cost_usd)
        _require_positive("ledger_lock_timeout", self.ledger_lock_timeout)
        if self.temperature < 0.0:
            raise VeridianConfigError(
                f"VeridianConfig.temperature must be >= 0, got {self.temperature!r}"
            )
