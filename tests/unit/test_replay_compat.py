"""
tests.unit.test_replay_compat
──────────────────────────────
RV3-003: Global replay compatibility envelope.

Unit tests for veridian.loop.replay_compat — generalizes the PRM-only replay
snapshot into a runner-level invariant applied to every task. Snapshot hashes
{model_id, provider_version, prompt_hash, verifier_id, verifier_config_hash,
tool_allowlist_hash} so restarts fail closed when any of these change in
strict mode.
"""

from __future__ import annotations

from typing import Any

from veridian.core.task import Task
from veridian.loop.replay_compat import (
    ReplaySnapshot,
    build_run_replay_snapshot,
    check_replay_compatibility,
)
from veridian.providers.base import LLMProvider, LLMResponse, Message


class _StubProvider(LLMProvider):
    """Minimal in-memory provider for snapshot tests."""

    def __init__(self, model: str = "stub/v1") -> None:
        self.model = model

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="ok", model=self.model)

    async def complete_async(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        return self.complete(messages, **kwargs)


def _make_task(
    verifier_id: str = "schema",
    verifier_config: dict[str, Any] | None = None,
    title: str = "t1",
    description: str = "desc",
) -> Task:
    return Task(
        id="t1",
        title=title,
        description=description,
        verifier_id=verifier_id,
        verifier_config=verifier_config or {"required_fields": ["summary"]},
    )


class TestBuildRunReplaySnapshot:
    def test_snapshot_is_deterministic_for_same_inputs(self) -> None:
        task = _make_task()
        provider = _StubProvider()
        a = build_run_replay_snapshot(task, provider)
        b = build_run_replay_snapshot(task, provider)
        assert a == b

    def test_snapshot_differs_when_model_changes(self) -> None:
        task = _make_task()
        a = build_run_replay_snapshot(task, _StubProvider(model="stub/v1"))
        b = build_run_replay_snapshot(task, _StubProvider(model="stub/v2"))
        assert a != b
        assert a.model_id != b.model_id

    def test_snapshot_differs_when_verifier_id_changes(self) -> None:
        a = build_run_replay_snapshot(_make_task(verifier_id="schema"), _StubProvider())
        b = build_run_replay_snapshot(_make_task(verifier_id="bash_exit"), _StubProvider())
        assert a != b
        assert a.verifier_id != b.verifier_id

    def test_snapshot_differs_when_verifier_config_changes(self) -> None:
        a = build_run_replay_snapshot(
            _make_task(verifier_config={"required_fields": ["summary"]}),
            _StubProvider(),
        )
        b = build_run_replay_snapshot(
            _make_task(verifier_config={"required_fields": ["summary", "detail"]}),
            _StubProvider(),
        )
        assert a != b
        assert a.verifier_config_hash != b.verifier_config_hash

    def test_snapshot_differs_when_task_description_changes(self) -> None:
        a = build_run_replay_snapshot(_make_task(description="v1"), _StubProvider())
        b = build_run_replay_snapshot(_make_task(description="v2"), _StubProvider())
        assert a != b
        assert a.prompt_hash != b.prompt_hash

    def test_snapshot_round_trips_dict(self) -> None:
        task = _make_task()
        snap = build_run_replay_snapshot(task, _StubProvider())
        d = snap.to_dict()
        assert isinstance(d, dict)
        restored = ReplaySnapshot.from_dict(d)
        assert restored == snap


class TestCheckReplayCompatibility:
    def test_returns_none_when_saved_is_none_first_run(self) -> None:
        task = _make_task()
        current = build_run_replay_snapshot(task, _StubProvider())
        assert check_replay_compatibility(task, current, saved=None, strict=True) is None

    def test_returns_none_when_strict_false_and_mismatch(self) -> None:
        task = _make_task()
        current = build_run_replay_snapshot(task, _StubProvider(model="stub/v1"))
        saved = build_run_replay_snapshot(task, _StubProvider(model="stub/v2")).to_dict()
        assert check_replay_compatibility(task, current, saved=saved, strict=False) is None

    def test_returns_error_when_strict_true_and_model_mismatch(self) -> None:
        task = _make_task()
        current = build_run_replay_snapshot(task, _StubProvider(model="stub/v1"))
        saved = build_run_replay_snapshot(task, _StubProvider(model="stub/v2")).to_dict()
        error = check_replay_compatibility(task, current, saved=saved, strict=True)
        assert error is not None
        assert "replay_incompatible" in error
        assert "model_id" in error

    def test_returns_error_when_verifier_config_changed(self) -> None:
        current = build_run_replay_snapshot(
            _make_task(verifier_config={"required_fields": ["summary"]}),
            _StubProvider(),
        )
        saved = build_run_replay_snapshot(
            _make_task(verifier_config={"required_fields": ["summary", "extra"]}),
            _StubProvider(),
        ).to_dict()
        error = check_replay_compatibility(_make_task(), current, saved=saved, strict=True)
        assert error is not None
        assert "verifier_config_hash" in error

    def test_returns_none_when_strict_true_and_all_fields_match(self) -> None:
        task = _make_task()
        current = build_run_replay_snapshot(task, _StubProvider())
        saved = current.to_dict()
        assert check_replay_compatibility(task, current, saved=saved, strict=True) is None

    def test_error_message_fits_in_llm_context(self) -> None:
        """Error strings are injected into agent prompts — must be < 300 chars."""
        task = _make_task()
        current = build_run_replay_snapshot(task, _StubProvider(model="stub/v1"))
        saved = build_run_replay_snapshot(task, _StubProvider(model="stub/v999")).to_dict()
        error = check_replay_compatibility(task, current, saved=saved, strict=True)
        assert error is not None
        assert len(error) < 300
