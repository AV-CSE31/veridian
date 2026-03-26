"""
tests.unit.test_agents
───────────────────────
Unit tests for BaseAgent, WorkerAgent, InitializerAgent, ReviewerAgent.
"""

import json

import pytest

from veridian.agents.base import BaseAgent
from veridian.agents.worker import WorkerAgent
from veridian.core.config import VeridianConfig
from veridian.core.task import Task, TaskResult
from veridian.providers.base import LLMResponse
from veridian.providers.mock_provider import MockProvider

# ── BaseAgent ─────────────────────────────────────────────────────────────────


class TestBaseAgent:
    def test_base_agent_has_id_classvar(self):
        """BaseAgent must declare id as ClassVar."""
        assert hasattr(BaseAgent, "id")

    def test_concrete_agent_must_implement_run(self):
        """Concrete agents that don't implement run() raise TypeError."""

        class BadAgent(BaseAgent):
            id = "bad"

        with pytest.raises(TypeError):
            BadAgent()


# ── WorkerAgent ───────────────────────────────────────────────────────────────


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
        """WorkerAgent extracts structured output from <veridian:result> block."""
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
        """WorkerAgent result contains the raw LLM output."""
        payload = json.dumps({"summary": "ok", "structured": {}})
        mock_provider.script(
            [
                LLMResponse(content=f"<veridian:result>\n{payload}\n</veridian:result>"),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        result = agent.run(task)
        assert "veridian:result" in result.raw_output

    def test_exits_on_max_turns_without_result(self, config, mock_provider, task):
        """WorkerAgent returns a result (possibly empty structured) after max_turns."""
        config.max_turns_per_task = 2
        # Provide responses that have no result block — agent will exhaust turns
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
        """When no result or bash commands detected, agent appends prompt nudge."""
        config.max_turns_per_task = 3
        payload = json.dumps({"summary": "done", "structured": {}})
        mock_provider.script(
            [
                LLMResponse(content="I'm done"),  # no result block
                LLMResponse(content=f"<veridian:result>\n{payload}\n</veridian:result>"),
            ]
        )
        agent = WorkerAgent(provider=mock_provider, config=config)
        agent.run(task)
        # Should have made 2 calls
        assert mock_provider.call_count == 2

    def test_result_regex_matches_veridian_block(self):
        """The result regex matches a valid veridian:result block."""
        from veridian.agents.worker import _RESULT_RE

        content = '<veridian:result>\n{"summary": "ok", "structured": {}}\n</veridian:result>'
        match = _RESULT_RE.search(content)
        assert match is not None
        data = json.loads(match.group(1))
        assert data["summary"] == "ok"

    def test_result_regex_does_not_match_partial(self):
        """The result regex does not match incomplete blocks."""
        from veridian.agents.worker import _RESULT_RE

        content = '<veridian:result>{"summary": "ok"}'  # no closing tag
        match = _RESULT_RE.search(content)
        assert match is None

    def test_worker_agent_id(self):
        assert WorkerAgent.id == "worker"


# ── InitializerAgent ──────────────────────────────────────────────────────────


class TestInitializerAgent:
    def test_initializer_agent_id(self):
        from veridian.agents.initializer import InitializerAgent

        assert InitializerAgent.id == "initializer"

    def test_initializer_run_returns_task(self):
        from veridian.agents.initializer import InitializerAgent

        mock = MockProvider()
        config = VeridianConfig()
        agent = InitializerAgent(provider=mock, config=config)
        task = Task(title="test")
        result = agent.run(task)
        assert result is not None


# ── ReviewerAgent ─────────────────────────────────────────────────────────────


class TestReviewerAgent:
    def test_reviewer_agent_id(self):
        from veridian.agents.reviewer import ReviewerAgent

        assert ReviewerAgent.id == "reviewer"

    def test_reviewer_run_returns_task_result(self):
        from veridian.agents.reviewer import ReviewerAgent

        mock = MockProvider()
        config = VeridianConfig()
        agent = ReviewerAgent(provider=mock, config=config)
        task = Task(title="test")
        result_in = TaskResult(raw_output="done", structured={"answer": "42"})
        reviewed = agent.run(task, result_in)
        assert isinstance(reviewed, TaskResult)
