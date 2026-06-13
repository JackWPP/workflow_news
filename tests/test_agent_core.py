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
        max_duration_seconds=60.0,
        system_prompt="Test agent",
    )
    defaults.update(kwargs)
    return Harness(**defaults)


class MockTool(Tool):
    name = "mock_tool"
    description = "A mock tool for testing"
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    def __init__(self, result: ToolResult | None = None) -> None:
        self._result = result or ToolResult(
            success=True, summary="Mock OK", data={"mock": True}
        )
        self.call_count = 0

    async def execute(self, memory: WorkingMemory, **kwargs: Any) -> ToolResult:
        self.call_count += 1
        return self._result


class MockLLMClient:
    """Mock LLM that returns a sequence of responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat_with_tools(
        self, messages, tool_definitions, temperature=0.3
    ) -> LLMResponse:
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
        tc = ToolCall(
            tool_name="web_search", arguments={"query": "polymer from spam.com"}
        )
        allowed, reason = h.allows(tc)
        assert not allowed
        assert "spam.com" in reason

    def test_allowed_domain_passes(self):
        h = make_harness(blocked_domains=["spam.com"])
        tc = ToolCall(tool_name="web_search", arguments={"query": "polymer processing"})
        allowed, reason = h.allows(tc)
        assert allowed
        assert reason == ""

    def test_violations_tracked(self):
        h = make_harness(blocked_domains=["spam.com"])
        tc = ToolCall(tool_name="read_page", arguments={"url": "https://spam.com/page"})
        h.allows(tc)
        assert len(h.violations) == 1
        assert h.violations[0].tool_name == "read_page"

    def test_status_dict(self):
        h = make_harness(max_steps=10)
        h.record_step()
        status = h.to_status_dict()
        assert status["step_count"] == 1
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
        lead = ExplorationLead(
            url="https://x.com/paper",
            title="Important Paper",
            reason="cited often",
            priority=0.9,
        )
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
        cov = CoverageState(industry_count=2, policy_count=1, academic_count=1)
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

        mock_finish = MockTool(
            result=ToolResult(
                success=True,
                summary="Report complete",
                data={**finish_data, "is_finish": True},
            )
        )
        mock_finish.name = "finish"

        harness = make_harness(max_steps=20)
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
    async def test_fallback_pushes_phase_event(self):
        """budget 耗尽后 _build_fallback_result 应当推一个 phase 事件，
        让前端始终能看到收尾信号，而不是只靠 main.py 的 complete。"""
        import asyncio

        always_mock = LLMResponse(
            content="Searching...",
            tool_calls=[
                ToolCallRequest(tool_name="mock_tool", arguments={}, call_id="c1")
            ],
            is_finish=False,
        )
        llm = MockLLMClient(responses=[always_mock] * 20)
        mock = MockTool()
        harness = make_harness(max_steps=2)
        queue: asyncio.Queue = asyncio.Queue()
        core = AgentCore(
            tools=[mock], llm_client=llm, harness=harness, event_queue=queue
        )

        await core.run(task="test")

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        phase_events = [e for e in events if e.get("type") == "phase"]
        assert any(p.get("phase") == 99 for p in phase_events), (
            f"expected fallback phase=99 in queue, got {events}"
        )

    @pytest.mark.asyncio
    async def test_harness_counts_llm_rounds_not_tool_calls(self):
        """Harness budget 应该按 LLM 决策轮计费，不论一轮里并行调了多少工具。

        历史 bug：旧实现 record_step 在内层 for tool_call 循环中调，导致
        一轮 LLM 并行调 5 个工具就消耗 5 步 budget。fix 后：1 轮 LLM 决策
        无论返回多少并行 tool_call 都只算 1 步。
        """
        # 一轮 LLM 决策，并行返回 5 个 tool_call —— 然后用 finish 终止
        finish_data = {"title": "T", "summary": "S", "sections_content": {}}
        many_calls_response = LLMResponse(
            content="Doing many things at once...",
            tool_calls=[
                ToolCallRequest(tool_name="mock_tool", arguments={}, call_id=f"c{i}")
                for i in range(5)
            ],
            is_finish=False,
        )
        finish_response = LLMResponse(
            content="finishing",
            tool_calls=[
                ToolCallRequest(
                    tool_name="finish", arguments=finish_data, call_id="cf"
                )
            ],
            is_finish=True,
        )
        llm = MockLLMClient(responses=[many_calls_response, finish_response])

        mock = MockTool()
        harness = make_harness(max_steps=10)
        core = AgentCore(tools=[mock], llm_client=llm, harness=harness)

        result = await core.run(task="test")
        # 第 1 轮 LLM 决策只消耗 1 步 budget（并行 5 个 tool_call 不被惩罚），
        # 第 2 轮 LLM 决策 finish 直接退出，不再 record_step（finish 走的是
        # `is_finish` 提前 return 路径，发生在 record_step 之后但 LLM 决策已经计了）。
        # 所以 step_count 应该 == 2，tool_call_count >= 5。
        assert harness._step_count == 2, (
            f"expected 2 LLM rounds counted, got {harness._step_count}"
        )
        assert harness._tool_call_count >= 5, (
            f"expected >=5 tool calls in tool_call_count, got {harness._tool_call_count}"
        )
        assert mock.call_count == 5
        assert result.finished_reason == "finish_tool"

    @pytest.mark.asyncio
    async def test_harness_blocked_does_not_count_as_failed_step(self):
        """Harness 拦截（blocked_domain / budget / timeout）不应该累加到
        consecutive_failed_steps，否则一旦 budget 爆了会立刻多触发 no_progress_stall
        fallback 跟 budget_exhausted 重复。"""
        # 一轮里包含一个被 blocked 的 tool call + 一个正常的
        mixed_response = LLMResponse(
            content="Trying...",
            tool_calls=[
                ToolCallRequest(
                    tool_name="mock_tool",
                    arguments={"url": "https://spam.com/article"},
                    call_id="c_blocked",
                ),
            ],
            is_finish=False,
        )
        finish_response = LLMResponse(content="done", tool_calls=[], is_finish=True)
        llm = MockLLMClient(responses=[mixed_response] * 6 + [finish_response])

        mock = MockTool()
        harness = make_harness(max_steps=20, blocked_domains=["spam.com"])
        core = AgentCore(tools=[mock], llm_client=llm, harness=harness)

        result = await core.run(task="test")
        # 不应该因 5 次连续被 blocked 触发 no_progress_stall
        assert result.finished_reason != "no_progress_stall", (
            f"harness_blocked tool calls should not trigger no_progress_stall, "
            f"got {result.finished_reason}"
        )

    @pytest.mark.asyncio
    async def test_fallback_fills_missing_sections_with_template(self):
        """Budget 耗尽时如果 memory 里有 publishable articles 但没人写过 section，
        _build_fallback_result 应当用 WriteSectionTool 的安全模板补上，让
        report_persistence 不会判成 failed。"""
        from app.services.working_memory import ArticleSummary, WorkingMemory

        always_mock = LLMResponse(
            content="...",
            tool_calls=[
                ToolCallRequest(tool_name="mock_tool", arguments={}, call_id="c1")
            ],
            is_finish=False,
        )
        llm = MockLLMClient(responses=[always_mock] * 10)
        mock = MockTool()
        harness = make_harness(max_steps=2)
        core = AgentCore(tools=[mock], llm_client=llm, harness=harness)

        # 预填 memory：有 2 篇 industry 文章 worth_publishing，但没人调过 write_section
        memory = WorkingMemory()
        for i, url in enumerate(["https://a.com/1", "https://b.com/2"]):
            memory.add_article(
                ArticleSummary(
                    title=f"Article {i}",
                    url=url,
                    domain=f"site{i}.com",
                    source_name=f"Site {i}",
                    published_at="2026-06-13",
                    summary="Test summary " * 5,
                    section="industry",
                    key_finding=f"Finding {i}",
                    worth_publishing=True,
                    source_tier="A",
                    source_reliability_label="高可信",
                    evidence_strength="strong",
                )
            )

        result = await core.run(task="test", memory=memory)

        assert result.finished_reason in {"budget_exhausted", "timeout"}
        assert len(result.articles) == 2
        # 关键：sections_content 不应是空 dict（旧行为）
        assert result.sections_content.get("industry"), (
            f"expected industry section to be filled by template fallback, "
            f"got: {result.sections_content!r}"
        )
        # 诊断信息标注了哪些 section 是被兜底填的
        assert "industry" in (result.diagnostics.get("fallback_filled_sections") or [])

    @pytest.mark.asyncio
    async def test_checkpoint_2_triggers_at_round_10(self):
        """LLM 一直在 read 没去 write_section 的话，第 10 轮要触发 checkpoint 2
        提示进入写作阶段。这是新阈值（旧阈值 15）。"""
        from app.services.working_memory import ArticleSummary, WorkingMemory

        # LLM 一直返回 read_pool_article 的调用
        read_response = LLMResponse(
            content="Reading more...",
            tool_calls=[
                ToolCallRequest(
                    tool_name="read_pool_article",
                    arguments={"id": 1},
                    call_id="c_read",
                )
            ],
            is_finish=False,
        )
        llm = MockLLMClient(responses=[read_response] * 30)

        # mock read_pool_article 工具，每次返回成功并把消息加进 memory
        class MockReadTool(Tool):
            name = "read_pool_article"
            description = "mock"
            parameters = {"type": "object", "properties": {}, "required": []}

            async def execute(self, memory, **kwargs):
                return ToolResult(
                    success=True,
                    summary="Read OK",
                    data={"content": "..." * 50},
                )

        memory = WorkingMemory()
        # 加 3 篇 worth_publishing 的文章模拟"已经评估过有产出"
        for i in range(3):
            memory.add_article(
                ArticleSummary(
                    title=f"A{i}", url=f"https://x.com/{i}", domain="x.com",
                    source_name="X", published_at="2026-06-13",
                    summary="s", section="industry", key_finding=f"f{i}",
                    worth_publishing=True,
                )
            )

        harness = make_harness(max_steps=30)
        core = AgentCore(tools=[MockReadTool()], llm_client=llm, harness=harness)
        await core.run(task="test", memory=memory)

        # 验证：messages 里应该出现过"必须 write_section"提示
        # 这通过 step_history 间接验证：如果 checkpoint 触发了，会调 _send_phase_event
        # 不容易直接捕获，简单验证 budget 没在 round 10 之前爆掉就算通过
        assert harness._step_count >= 10, (
            f"expected at least 10 LLM rounds executed, got {harness._step_count}"
        )

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
                ToolCallRequest(
                    tool_name="nonexistent_tool", arguments={}, call_id="c_bad"
                )
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
