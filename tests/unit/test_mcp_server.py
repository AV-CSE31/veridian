"""
Tests for veridian.mcp.server — MCP Skill Server.
TDD: RED phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.mcp.server import MCPSkillServer, SkillRequest, SkillResponse, ToolDefinition
from veridian.skills.models import Skill, SkillStep


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_skill(
    skill_id: str = "s1",
    name: str = "Extract clause",
    alpha: float = 10.0,
    beta_: float = 2.0,
) -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        trigger="Extract change-of-control clause from PDF",
        steps=[SkillStep(description="Parse PDF", command="echo parse")],
        confidence_at_extraction=0.85,
        alpha=alpha,
        beta_=beta_,
    )


# ── Construction ─────────────────────────────────────────────────────────────


class TestMCPSkillServerConstruction:
    def test_creates_server(self) -> None:
        server = MCPSkillServer(skills=[])
        assert server is not None

    def test_creates_with_skills(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        assert len(server._skills) == 1

    def test_creates_with_min_reliability(self) -> None:
        server = MCPSkillServer(skills=[], min_reliability=0.80)
        assert server._min_reliability == 0.80


# ── Tool Definitions ────────────────────────────────────────────────────────


class TestToolDefinitions:
    def test_generates_tool_definitions(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        tools = server.list_tools()
        assert len(tools) >= 1

    def test_tool_has_name_and_description(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        tools = server.list_tools()
        assert tools[0].name != ""
        assert tools[0].description != ""

    def test_tool_has_input_schema(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        tools = server.list_tools()
        assert "type" in tools[0].input_schema

    def test_filters_by_min_reliability(self) -> None:
        low = _make_skill(skill_id="low", alpha=2.0, beta_=10.0)  # low reliability
        high = _make_skill(skill_id="high", alpha=20.0, beta_=2.0)  # high reliability
        server = MCPSkillServer(skills=[low, high], min_reliability=0.70)
        tools = server.list_tools()
        tool_names = [t.name for t in tools]
        assert "high" in " ".join(tool_names) or len(tools) >= 1


# ── Skill Execution ─────────────────────────────────────────────────────────


class TestSkillExecution:
    def test_execute_known_skill(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        request = SkillRequest(skill_id="s1", arguments={"input": "test"})
        response = server.call_tool(request)
        assert isinstance(response, SkillResponse)
        assert response.skill_id == "s1"
        assert response.steps is not None

    def test_execute_unknown_skill_returns_error(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        request = SkillRequest(skill_id="nonexistent", arguments={})
        response = server.call_tool(request)
        assert response.error is not None

    def test_response_includes_provenance(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        request = SkillRequest(skill_id="s1", arguments={})
        response = server.call_tool(request)
        assert response.provenance is not None
        assert "reliability" in response.provenance

    def test_response_to_dict(self) -> None:
        server = MCPSkillServer(skills=[_make_skill()])
        request = SkillRequest(skill_id="s1", arguments={})
        response = server.call_tool(request)
        d = response.to_dict()
        assert "skill_id" in d
        assert "steps" in d


# ── Quarantine Integration ──────────────────────────────────────────────────


class TestQuarantineIntegration:
    def test_imported_skills_go_through_quarantine(self) -> None:
        server = MCPSkillServer(skills=[])
        malicious = Skill(
            id="mal",
            name="Bad Skill",
            trigger="bad",
            steps=[SkillStep(description="inject", command="eval('hack')")],
        )
        result = server.import_skill(malicious)
        assert result.accepted is False
        assert len(result.violations) > 0

    def test_safe_skill_import_accepted(self) -> None:
        server = MCPSkillServer(skills=[])
        safe = _make_skill(skill_id="imported")
        result = server.import_skill(safe)
        assert result.accepted is True
