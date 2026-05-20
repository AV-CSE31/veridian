"""Unit tests for the runner worker."""

import json

import pytest

from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult
from veridian.loop.worker import WorkerAgent
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider


class TestWorkerAgent:
    @pytest.fixture
    def config(self) -> VeridianConfig:
        return VeridianConfig(max_turns_per_task=5)

    @pytest.fixture
    def mock_provider(self) -> MockProvider:
        return MockProvider()

    @pytest.fixture
    def task(self) -> Task:
        return Task(
            id="t1",
            title="Test task",
            description="Do the thing",
            verifier_id="schema",
        )

    def test_extracts_result_from_veridian_block(self, config, mock_provider, task):
        payload = json.dumps({"summary": "done", "structured": {"answer": "42"}})
        mock_provider.script(
            [
                LLMResponse(content=f"<veridian:result>\n{payload}\n</veridian:result>"),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        result = agent.run(task)
        assert result.structured.get("answer") == "42"

    def test_result_has_raw_output(self, config, mock_provider, task):
        payload = json.dumps({"summary": "ok", "structured": {}})
        mock_provider.script(
            [
                LLMResponse(content=f"<veridian:result>\n{payload}\n</veridian:result>"),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        result = agent.run(task)
        assert "veridian:result" in result.raw_output

    def test_worker_captures_tool_calls_and_timing(self, config, mock_provider, task):
        payload = json.dumps({"summary": "ok", "structured": {"x": 1}})
        mock_provider.script(
            [
                LLMResponse(
                    content=f"<veridian:result>\n{payload}\n</veridian:result>",
                    input_tokens=12,
                    output_tokens=7,
                    tool_calls=[{"name": "shell"}],
                ),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        result = agent.run(task)
        assert result.tool_calls == [{"name": "shell"}]
        assert result.token_usage["total_tokens"] == 19
        assert "worker_ms" in result.timing
        assert len(result.trace_steps) >= 1
        assert result.trace_steps[0].action_type == "reason"

    def test_exits_on_max_turns_without_result(self, config, mock_provider, task):
        config.max_turns_per_task = 2
        mock_provider.script(
            [
                LLMResponse(content="Thinking..."),
                LLMResponse(content="Still thinking..."),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        result = agent.run(task)
        assert isinstance(result, TaskResult)

    def test_prompts_for_result_when_no_output(self, config, mock_provider, task):
        config.max_turns_per_task = 3
        payload = json.dumps({"summary": "done", "structured": {}})
        mock_provider.script(
            [
                LLMResponse(content="I'm done"),
                LLMResponse(content=f"<veridian:result>\n{payload}\n</veridian:result>"),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        agent.run(task)
        assert mock_provider.call_count == 2

    def test_result_regex_matches_veridian_block(self):
        from veridian.loop.worker import _RESULT_RE

        content = '<veridian:result>\n{"summary": "ok", "structured": {}}\n</veridian:result>'
        match = _RESULT_RE.search(content)
        assert match is not None
        data = json.loads(match.group(1))
        assert data["summary"] == "ok"

    def test_result_regex_does_not_match_partial(self):
        from veridian.loop.worker import _RESULT_RE

        content = '<veridian:result>{"summary": "ok"}'
        match = _RESULT_RE.search(content)
        assert match is None

    def test_worker_agent_id(self):
        assert WorkerAgent.id == "worker"
