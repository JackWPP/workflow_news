"""
tests/test_agent_core.py — Agent Core 单元测试

测试 AgentCore、Harness、WorkingMemory 的核心逻辑：
  - Harness 预算控制和拦截
  - WorkingMemory 状态记录
  - AgentCore 工具路由
  - 兜底结果构建
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent_core import AgentCore, AgentResult
from app.services.harness import Harness, HarnessViolation
from app.services.llm_client import LLMClient, LLMResponse, ToolCallRequest
from app.services.tools import FinishTool, Tool, ToolCall, ToolResult
from app.services.working_memory import (
    ArticleSummary,
    CoverageState,
    WorkingMemory,
)


# ── Fixtures ──────────────────────────────────────────────

def make_article(section: str = "industry", url: str | None = None) -> ArticleSummary:
    return ArticleSummary(
        title=f"Test Article ({section})",
        url=url or f"https://example.com/{section}",
        domain="example.com",
        source_name="Example",
        published_at="2026-04-01",
        summary="Test summary",
        section=section,
        key_finding="Test finding",
        worth_publishing=True,
    )


def make_harness(**kwargs: Any) -> Harness:
    defaults = dict(
        max_steps=10,
        max_search_calls=5,
        max_page_reads=5,
        max_duration_seconds=60.0,
        max_llm_calls=10,
        system_prompt="Test agent",
    )
    defaults.update(kwargs)
    return Harness(**defaults)


class MockTool(Tool):
    name = "mock_tool"
    description = "A mock tool for testing"
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    def __init__(self, result: ToolResult | None = None) -> None:
        self._result = result or ToolResult(success=True, summary="Mock OK", data={"mock": True})
        self.call_count = 0

    async def execute(self, memory: WorkingMemory, **kwargs: Any) -> ToolResult:
        self.call_count += 1
        return self._result


class MockLLMClient:
    """Mock LLM that returns a sequence of responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat_with_tools(self, messages, tool_definitions, temperature=0.3) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        # Default: finish
        return LLMResponse(content="Done", tool_calls=[], is_finish=True)

    def build_tool_result_message(self, tool_call_id: str, result_content: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result_content}


# ── Harness Tests ─────────────────────────────────────────

class TestHarness:
    def test_initial_budget(self):
        h = make_harness(max_steps=5)
        assert h.budget_remaining == 5

    def test_budget_decrements_with_steps(self):
        h = make_harness(max_steps=5)
        h.record_step()
        h.record_step()
        assert h.budget_remaining == 3

    def test_budget_exhausted_blocks(self):
        h = make_harness(max_steps=2)
        h.record_step()
        h.record_step()
        assert h.budget_remaining == 0

    def test_blocked_domain_denied(self):
        h = make_harness(blocked_domains=["spam.com"])
        tc = ToolCall(tool_name="web_search", arguments={"query": "polymer from spam.com"})
        allowed, reason = h.allows(tc)
        assert not allowed
        assert "spam.com" in reason

    def test_allowed_domain_passes(self):
        h = make_harness(blocked_domains=["spam.com"])
        tc = ToolCall(tool_name="web_search", arguments={"query": "polymer processing"})
        allowed, reason = h.allows(tc)
        assert allowed
        assert reason == ""

    def test_search_quota_blocks(self):
        h = make_harness(max_search_calls=2)
        h.record_search()
        h.record_search()
        tc = ToolCall(tool_name="web_search", arguments={"query": "test"})
        allowed, reason = h.allows(tc)
        assert not allowed
        assert "quota" in reason

    def test_read_quota_blocks(self):
        h = make_harness(max_page_reads=2)
        h.record_read()
        h.record_read()
        tc = ToolCall(tool_name="read_page", arguments={"url": "https://example.com"})
        allowed, reason = h.allows(tc)
        assert not allowed

    def test_violations_tracked(self):
        h = make_harness(blocked_domains=["spam.com"])
        tc = ToolCall(tool_name="read_page", arguments={"url": "https://spam.com/page"})
        h.allows(tc)
        assert len(h.violations) == 1
        assert h.violations[0].tool_name == "read_page"

    def test_status_dict(self):
        h = make_harness(max_steps=10)
        h.record_step()
        h.record_search()
        status = h.to_status_dict()
        assert status["step_count"] == 1
        assert status["search_count"] == 1
        assert status["budget_remaining"] == 9


# ── WorkingMemory Tests ───────────────────────────────────

class TestWorkingMemory:
    def test_search_deduplication(self):
        mem = WorkingMemory()
        mem.record_search("polymer processing")
        assert mem.has_searched("polymer processing")
        assert mem.has_searched("POLYMER PROCESSING")  # case-insensitive
        assert not mem.has_searched("polymer recycling")

    def test_read_deduplication(self):
        mem = WorkingMemory()
        mem.record_read("https://example.com")
        assert mem.has_read("https://example.com")
        assert not mem.has_read("https://other.com")

    def test_add_article_updates_coverage(self):
        mem = WorkingMemory()
        mem.add_article(make_article("industry"))
        mem.add_article(make_article("policy"))
        assert mem.coverage.industry_count == 1
        assert mem.coverage.policy_count == 1
        assert mem.coverage.academic_count == 0

    def test_duplicate_article_not_added(self):
        mem = WorkingMemory()
        mem.add_article(make_article("industry"))
        mem.add_article(make_article("industry"))  # same URL
        assert len(mem.publishable_articles()) == 1

    def test_publishable_articles_filter(self):
        mem = WorkingMemory()
        art = make_article("industry")
        art.worth_publishing = False
        mem.add_article(art)
        assert len(mem.publishable_articles()) == 0

    def test_coverage_gaps(self):
        mem = WorkingMemory()
        mem.add_article(make_article("industry"))
        gaps = mem.coverage.gaps()
        assert any("板块" in g for g in gaps)

    def test_publishable_threshold(self):
        mem = WorkingMemory()
        assert not mem.coverage.is_publishable
        mem.add_article(make_article("industry", url="https://ex.com/1"))
        mem.add_article(make_article("industry", url="https://ex.com/2"))
        mem.add_article(make_article("industry", url="https://ex.com/3"))
        mem.add_article(make_article("policy", url="https://ex.com/4"))
        mem.add_article(make_article("academic", url="https://ex.com/5"))
        mem.add_article(make_article("industry", url="https://ex.com/6"))
        assert mem.coverage.is_publishable

    def test_context_summary_populated(self):
        mem = WorkingMemory()
        mem.record_search("test query")
        mem.add_article(make_article("industry"))
        summary = mem.to_context_summary()
        assert "搜索" in summary or "test query" in summary
        assert "1" in summary  # at least one article found

    def test_snapshot_serializable(self):
        import json
        mem = WorkingMemory()
        mem.add_article(make_article("policy"))
        snapshot = mem.snapshot()
        # Should be JSON-serializable
        json.dumps(snapshot)
        assert snapshot["publishable_count"] == 1

    def test_exploration_queue(self):
        from app.services.working_memory import ExplorationLead
        mem = WorkingMemory()
        lead = ExplorationLead(url="https://x.com/paper", title="Important Paper", reason="cited often", priority=0.9)
        mem.add_exploration_lead(lead)
        assert len(mem.exploration_queue) == 1
        picked = mem.pop_best_lead()
        assert picked is not None
        assert picked.url == "https://x.com/paper"
        assert len(mem.exploration_queue) == 0

    def test_rejected_direction_recorded(self):
        mem = WorkingMemory()
        mem.reject_direction("PR wire content, no value")
        assert "PR wire content, no value" in mem.rejected_directions


# ── CoverageState Tests ───────────────────────────────────

class TestCoverageState:
    def test_complete_detection(self):
        cov = CoverageState(
            academic_count=3,
            industry_count=3,
            policy_count=2,
            verified_image_count=3,
        )
        assert cov.is_complete

    def test_partial_detection(self):
        cov = CoverageState(industry_count=2, policy_count=1)
        assert cov.is_publishable
        assert not cov.is_complete

    def test_not_publishable(self):
        cov = CoverageState(industry_count=3)
        assert not cov.is_publishable

    def test_gaps_reported(self):
        cov = CoverageState(industry_count=1)
        gaps = cov.gaps()
        assert len(gaps) > 0


# ── AgentResult Tests ─────────────────────────────────────

class TestAgentResult:
    def test_publishable_with_articles(self):
        result = AgentResult(
            success=True,
            title="Test Report",
            summary="Test",
            articles=[{"title": "A", "url": "https://x.com"}],
            finished_reason="finish_tool",
        )
        assert result.is_publishable

    def test_not_publishable_empty(self):
        result = AgentResult(
            success=False,
            title="",
            summary="",
            finished_reason="budget_exhausted",
        )
        assert not result.is_publishable

    def test_debug_payload(self):
        result = AgentResult(
            success=True,
            title="Report",
            summary="Summary",
            articles=[{}],
            finished_reason="finish_tool",
            step_count=12,
            total_tokens=5000,
        )
        payload = result.to_debug_payload()
        assert payload["step_count"] == 12
        assert payload["total_tokens"] == 5000
        assert payload["article_count"] == 1


# ── AgentCore Integration Tests (no API calls) ────────────

class TestAgentCore:
    @pytest.mark.asyncio
    async def test_finish_tool_stops_loop(self):
        """Agent should stop when finish tool is called."""
        finish_data = {
            "title": "Test Report",
            "summary": "A test report",
            "sections_content": {"industry": "## Industry\n\nTest content"},
        }
        finish_response = LLMResponse(
            content="I'll finish the report now.",
            tool_calls=[
                ToolCallRequest(
                    tool_name="finish",
                    arguments=finish_data,
                    call_id="call_finish_1",
                )
            ],
            is_finish=True,
        )
        llm = MockLLMClient(responses=[finish_response])

        mock_finish = MockTool(result=ToolResult(
            success=True,
            summary="Report complete",
            data={**finish_data, "is_finish": True},
        ))
        mock_finish.name = "finish"

        harness = make_harness(max_steps=20)
        harness.min_searches_before_finish = 0
        harness.min_articles_before_finish = 0
        core = AgentCore(
            tools=[mock_finish],
            llm_client=llm,
            harness=harness,
        )

        result = await core.run(task="Generate daily report")
        # finish is extracted from LLM response directly, not via tool.execute()
        assert result.finished_reason == "finish_tool"

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self):
        """Agent should stop when budget runs out."""
        # LLM always calls mock_tool (never finish)
        always_mock = LLMResponse(
            content="Searching...",
            tool_calls=[
                ToolCallRequest(tool_name="mock_tool", arguments={}, call_id="c1")
            ],
            is_finish=False,
        )
        llm = MockLLMClient(responses=[always_mock] * 20)

        mock = MockTool()
        harness = make_harness(max_steps=3)
        core = AgentCore(tools=[mock], llm_client=llm, harness=harness)

        result = await core.run(task="test")
        assert result.finished_reason in {"budget_exhausted", "timeout"}

    @pytest.mark.asyncio
    async def test_harness_blocks_invalid_tool(self):
        """Harness should block calls to domains on blocklist."""
        blocked_response = LLMResponse(
            content="Reading page...",
            tool_calls=[
                ToolCallRequest(
                    tool_name="mock_tool",
                    arguments={"url": "https://spam.com/article"},
                    call_id="c_spam",
                )
            ],
            is_finish=False,
        )
        finish_response = LLMResponse(content="Done", tool_calls=[], is_finish=True)
        llm = MockLLMClient(responses=[blocked_response, finish_response])

        mock = MockTool()
        harness = make_harness(max_steps=10, blocked_domains=["spam.com"])
        core = AgentCore(tools=[mock], llm_client=llm, harness=harness)

        await core.run(task="test")
        # mock_tool should have been blocked, so call_count should be 0
        assert mock.call_count == 0
        assert len(harness.violations) == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_handled_gracefully(self):
        """Unknown tool name should not crash the agent."""
        bad_tool_response = LLMResponse(
            content="Using nonexistent tool",
            tool_calls=[
                ToolCallRequest(tool_name="nonexistent_tool", arguments={}, call_id="c_bad")
            ],
            is_finish=False,
        )
        finish_response = LLMResponse(content="Done", tool_calls=[], is_finish=True)
        llm = MockLLMClient(responses=[bad_tool_response, finish_response])

        harness = make_harness(max_steps=10)
        core = AgentCore(tools=[], llm_client=llm, harness=harness)

        # Should not raise
        result = await core.run(task="test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_tool_stall_finishes_early(self):
        llm = MockLLMClient(
            responses=[
                LLMResponse(content="thinking 1", tool_calls=[], is_finish=False),
                LLMResponse(content="thinking 2", tool_calls=[], is_finish=False),
                LLMResponse(content="thinking 3", tool_calls=[], is_finish=False),
            ]
        )
        harness = make_harness(max_steps=20, max_duration_seconds=300.0)
        core = AgentCore(tools=[], llm_client=llm, harness=harness)

        result = await core.run(task="test")

        assert result.finished_reason == "llm_no_tool_stall"
        assert result.diagnostics["llm_no_tool_stall_count"] == 1
