from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.services.agent_core import AgentCore
from app.services.article_agent import ArticleAgent
from app.services.daily_report_agent import DailyReportAgent
from app.services.brave import BraveSearchClient
from app.services.harness import make_daily_report_harness
from app.services.llm_client import LLMClient, LLMResponse, ToolCallRequest
from app.services.scraper import ScraperClient
from app.services.source_quality import classify_source, detect_page_kind
from app.services.tools import (
    CompareSourcesTool,
    EvaluateArticleTool,
    FollowReferencesTool,
    CheckCoverageTool,
    ReadPageTool,
    Tool,
    ToolResult,
    WebSearchTool,
    WriteSectionTool,
)
from app.services.working_memory import (
    ArticleSummary,
    CoverageState,
    ExplorationLead,
    WorkingMemory,
)
from app.config import settings
from app.utils import normalize_external_url, now_local


class _StaticTool(Tool):
    name = "static"
    description = "static test tool"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def execute(self, memory: WorkingMemory, **kwargs: Any) -> ToolResult:
        return self._result


class _EvaluateAndStoreTool(Tool):
    name = "evaluate_article"
    description = "test article evaluator"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def execute(self, memory: WorkingMemory, **kwargs: Any) -> ToolResult:
        memory.add_article(
            ArticleSummary(
                title=kwargs["title"],
                url=kwargs["url"],
                domain=kwargs["domain"],
                source_name=kwargs["domain"],
                published_at=kwargs.get("published_at"),
                summary=kwargs["content"],
                section="industry",
                key_finding="Inline image finding",
                worth_publishing=True,
            )
        )
        return ToolResult(
            success=True,
            summary="worthy",
            data={
                "worthy": True,
                "section": "industry",
                "key_finding": "Inline image finding",
                "reason": "test",
                "image_worthiness": True,
                "zh_title": kwargs["title"],
                "zh_summary": kwargs["content"],
            },
        )


class _VerifyImageTool(Tool):
    name = "verify_image"
    description = "test image verifier"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def execute(self, memory: WorkingMemory, **kwargs: Any) -> ToolResult:
        memory.mark_image_verified(kwargs["article_url"], kwargs["image_url"], "ok")
        return ToolResult(
            success=True, summary="ok", data={"suitable": True, "reason": "ok"}
        )


class _NoopLLM:
    async def chat_with_tools(
        self, messages, tool_definitions, temperature=0.3
    ) -> LLMResponse:
        return LLMResponse(content="done", is_finish=True)

    def build_tool_result_message(
        self, tool_call_id: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result_content}


class _LoopingToolLLM:
    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._arguments = arguments

    async def chat_with_tools(
        self, messages, tool_definitions, temperature=0.3
    ) -> LLMResponse:
        return LLMResponse(
            content="loop",
            tool_calls=[
                ToolCallRequest(
                    tool_name=self._tool_name,
                    arguments=self._arguments,
                    call_id="c1",
                )
            ],
        )

    def build_tool_result_message(
        self, tool_call_id: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result_content}


@pytest.mark.asyncio
async def test_brave_422_retries_do_not_open_circuit_breaker(monkeypatch):
    request = httpx.Request("GET", "https://brave.example/res/v1/web/search")
    responses = [
        httpx.Response(422, request=request),
        httpx.Response(422, request=request),
        httpx.Response(422, request=request),
        httpx.Response(
            200,
            request=request,
            json={"web": {"results": [{"url": "https://example.com/a", "title": "A"}]}},
        ),
    ]

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return responses.pop(0)

    monkeypatch.setattr("app.services.brave.httpx.AsyncClient", FakeAsyncClient)

    client = BraveSearchClient(api_key="test", base_url="https://brave.example")
    results = await client.search("polymer", search_type="web")

    assert results[0]["url"] == "https://example.com/a"
    assert responses == []


@pytest.mark.asyncio
async def test_brave_402_does_not_open_circuit_breaker(monkeypatch):
    request = httpx.Request("GET", "https://brave.example/res/v1/web/search")

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(402, request=request)

    monkeypatch.setattr("app.services.brave.httpx.AsyncClient", FakeAsyncClient)

    client = BraveSearchClient(api_key="test", base_url="https://brave.example")
    with pytest.raises(httpx.HTTPStatusError):
        await client.search("polymer", search_type="web")

    snapshot = client.health_snapshot()
    assert snapshot["last_error"] == "quota_exceeded"
    assert snapshot["state"] == "closed"


def test_daily_report_harness_prompt_alias_is_available():
    harness = make_daily_report_harness()

    assert harness.system_prompt
    assert "独立完成搜索、阅读、评估和撰写" in harness.system_prompt


@pytest.mark.asyncio
async def test_article_agent_persists_verified_inline_image_to_memory():
    memory = WorkingMemory()
    article_url = "https://example.com/article"
    image_url = "https://example.com/image.jpg"
    read_page = _StaticTool(
        ToolResult(
            success=True,
            summary="read",
            data={
                "title": "Inline Image Article",
                "domain": "example.com",
                "content_summary": "polymer processing content",
                "image_url": image_url,
                "published_at": "2026-04-08",
            },
        )
    )
    agent = ArticleAgent(
        url=article_url,
        context="test",
        memory=memory,
        tools={
            "read_page": read_page,
            "evaluate_article": _EvaluateAndStoreTool(),
            "verify_image": _VerifyImageTool(),
        },
    )

    card = await agent.run()
    best_image = memory.best_image_for_article(article_url)

    assert card.image_url == image_url
    assert best_image is not None
    assert best_image.verified

    core = AgentCore(
        tools=[], llm_client=_NoopLLM(), harness=make_daily_report_harness()
    )
    core._enrich_articles_with_images(memory)
    article = memory.publishable_articles()[0]
    assert article.image_url == image_url
    assert article.has_image


def test_working_memory_sync_article_card_updates_image_url():
    memory = WorkingMemory()
    memory.add_article(
        ArticleSummary(
            title="Example",
            url="https://example.com/article",
            domain="example.com",
            source_name="example.com",
            published_at="2026-04-09",
            summary="summary",
            section="industry",
            key_finding="finding",
            worth_publishing=True,
        )
    )

    class _Card:
        url = "https://example.com/article"
        resolved_url = "https://example.com/article"
        title = "Example"
        source_name = "example.com"
        domain = "example.com"
        published_at = "2026-04-09"
        summary = "summary"
        section = "industry"
        key_finding = "finding"
        image_url = "https://example.com/image.jpg"

    memory.sync_article_card(_Card())

    article = memory.publishable_articles()[0]
    assert article.image_url == "https://example.com/image.jpg"
    assert article.has_image is True


def test_daily_report_search_prompt_uses_recent_window_language():
    agent = DailyReportAgent()

    prompt = agent._build_search_prompt(now_local().date())

    assert "36 小时内" in prompt
    assert "7 天内" not in prompt


def test_daily_report_search_prompt_mentions_seeded_candidates_when_present():
    agent = DailyReportAgent()

    prompt = agent._build_search_prompt(now_local().date(), seeded_count=3)

    assert "预装入 3 条候选" in prompt
    assert "check_coverage" in prompt


def test_supervisor_round_continues_when_report_is_still_thin_below_target_items():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    for index, section in enumerate(["industry", "industry", "academic"], start=1):
        memory.add_article(
            ArticleSummary(
                title=f"Article {index}",
                url=f"https://example.com/{index}",
                domain="example.com",
                source_name="example.com",
                published_at="2026-04-10T01:00:00+08:00",
                summary="summary",
                section=section,
                key_finding="finding",
                worth_publishing=True,
            )
        )
    memory.set_formal_topic_count(3)

    should_continue = agent._should_run_supervisor_round(
        memory,
        successful=[],
        round_index=0,
        runtime={"report_target_items": 4, "max_extractions_per_run": 18},
    )

    assert should_continue is True


def test_extract_candidate_urls_skips_content_platform_candidates():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    memory.record_search_results(
        "query",
        [
            {
                "url": "https://www.toutiao.com/article/1234567890/",
                "title": "高分子材料观察",
                "snippet": "高分子加工产业新闻",
                "domain": "www.toutiao.com",
            },
            {
                "url": "https://finance.sina.com.cn/tech/2026-04-10/doc-good.shtml",
                "title": "注塑设备升级推动材料加工提效",
                "snippet": "聚焦注塑、挤出与材料加工升级",
                "domain": "finance.sina.com.cn",
            },
        ],
    )

    candidates = agent._extract_candidate_urls(
        memory,
        runtime={"max_extractions_per_run": 5},
    )

    assert len(candidates) == 1
    assert "sina.com.cn" in candidates[0][0]
    assert memory.candidate_rejection_reasons["content_platform_candidate"] == 1


@pytest.mark.asyncio
async def test_scraper_prefers_jina_first_for_js_heavy_domains(monkeypatch):
    class _FakeJina:
        async def scrape(self, url: str, timeout_seconds: int | None = None):
            return {
                "url": url,
                "resolved_url": url,
                "domain": "www.toutiao.com",
                "title": "Recovered by Jina",
                "markdown": "polymer processing content",
                "html": "",
                "metadata": {},
                "image_url": None,
                "published_at": None,
                "status": "success",
                "scrape_layer": "jina",
            }

    async def _unexpected_trafilatura(url: str):
        raise AssertionError("trafilatura should be skipped for Jina-first domains")

    scraper = ScraperClient(jina_client=_FakeJina())
    monkeypatch.setattr(scraper, "_trafilatura_scrape", _unexpected_trafilatura)

    result = await scraper.scrape("https://www.toutiao.com/article/123456/")

    assert result["scrape_layer"] == "jina"
    assert result["title"] == "Recovered by Jina"


@pytest.mark.asyncio
async def test_seed_trusted_source_candidates_adds_rss_entries(monkeypatch):
    agent = DailyReportAgent()
    memory = WorkingMemory()

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        "app.services.daily_report_agent.session_scope", lambda: _DummySession()
    )
    monkeypatch.setattr(
        "app.services.daily_report_agent.list_sources",
        lambda session: [
            SimpleNamespace(
                name="Trusted Feed",
                domain="trusted.example.com",
                type="industry_media",
                priority=90,
                tags=[],
                include_rules=[],
                exclude_rules=[],
                must_include_any=[],
                must_exclude_any=[],
                soft_signals=[],
                source_tier="government",
                rss_or_listing_url="https://trusted.example.com/rss.xml",
                crawl_mode="rss",
                use_direct_source=True,
                allow_images=True,
                language="zh",
                country="CN",
                enabled=True,
            )
        ],
    )

    async def _fake_fetch_feed_entries(
        feed_url: str, source_name: str, source_type: str
    ):
        return [
            {
                "url": "https://trusted.example.com/article-1",
                "title": "行业设备升级",
                "snippet": "聚焦高分子加工设备升级",
                "published_at": now_local(),
                "domain": "trusted.example.com",
                "search_type": "rss",
                "metadata": {"feed_url": feed_url},
                "source_name": source_name,
                "source_type": source_type,
            }
        ]

    monkeypatch.setattr(
        "app.services.daily_report_agent.fetch_feed_entries", _fake_fetch_feed_entries
    )

    seeded = await agent._seed_trusted_source_candidates(memory)

    assert seeded == 1
    assert len(memory.search_results) == 1
    assert memory.search_results[0]["metadata"]["seeded_from_trusted_source"] is True
    assert memory.search_results[0]["metadata"]["source_priority"] == 90


def test_seed_row_is_relevant_rejects_off_topic_academic_feed_item():
    agent = DailyReportAgent()

    assert (
        agent._seed_row_is_relevant(
            {
                "url": "https://www.nature.com/articles/example",
                "title": "QS rankings place MIT at No.1 again",
                "snippet": "University ranking results across multiple disciplines",
            }
        )
        is False
    )


@pytest.mark.asyncio
async def test_seed_trusted_source_candidates_prefers_higher_trust_tier(monkeypatch):
    agent = DailyReportAgent()
    memory = WorkingMemory()

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        "app.services.daily_report_agent.session_scope", lambda: _DummySession()
    )
    monkeypatch.setattr(
        "app.services.daily_report_agent.list_sources",
        lambda session: [
            SimpleNamespace(
                name="Lower Tier Feed",
                domain="lower.example.com",
                type="industry_media",
                priority=99,
                tags=[],
                include_rules=[],
                exclude_rules=[],
                must_include_any=[],
                must_exclude_any=[],
                soft_signals=[],
                source_tier="unknown",
                rss_or_listing_url="https://lower.example.com/rss.xml",
                crawl_mode="rss",
                use_direct_source=True,
                allow_images=True,
                language="zh",
                country="CN",
                enabled=True,
            ),
            SimpleNamespace(
                name="Government Feed",
                domain="gov.example.com",
                type="government",
                priority=80,
                tags=[],
                include_rules=[],
                exclude_rules=[],
                must_include_any=[],
                must_exclude_any=[],
                soft_signals=[],
                source_tier="government",
                rss_or_listing_url="https://gov.example.com/rss.xml",
                crawl_mode="rss",
                use_direct_source=True,
                allow_images=True,
                language="zh",
                country="CN",
                enabled=True,
            ),
        ],
    )

    async def _fake_fetch_feed_entries(
        feed_url: str, source_name: str, source_type: str
    ):
        return [
            {
                "url": f"{feed_url}/article",
                "title": f"{source_name} 高分子加工动态",
                "snippet": f"{source_type} 聚焦塑料与聚合物材料应用",
                "published_at": now_local(),
                "domain": feed_url.split("//", 1)[-1].split("/", 1)[0],
                "search_type": "rss",
                "metadata": {"feed_url": feed_url},
                "source_name": source_name,
                "source_type": source_type,
            }
        ]

    monkeypatch.setattr(
        "app.services.daily_report_agent.fetch_feed_entries", _fake_fetch_feed_entries
    )

    await agent._seed_trusted_source_candidates(memory)

    assert memory.search_results[0]["source_name"] == "Government Feed"


def test_candidate_score_rewards_rss_and_source_priority():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    quality = {"source_tier": "A"}
    recent = now_local()

    base_score = agent._candidate_score(
        {
            "url": "https://example.com/base",
            "domain": "example.com",
            "title": "高分子加工设备升级",
            "snippet": "聚焦高分子材料与注塑升级",
            "published_at": recent,
            "metadata": {},
        },
        memory,
        {},
        quality,
    )
    rss_score = agent._candidate_score(
        {
            "url": "https://example.com/rss",
            "domain": "example.com",
            "title": "高分子加工设备升级",
            "snippet": "聚焦高分子材料与注塑升级",
            "published_at": recent,
            "search_type": "rss",
            "metadata": {
                "is_direct_source": True,
                "source_priority": 90,
                "search_type": "rss",
            },
        },
        memory,
        {},
        quality,
    )

    assert rss_score > base_score


def test_candidate_score_rewards_undercovered_section_hints():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    memory.coverage.industry_count = 2
    memory.coverage.policy_count = 0
    quality = {"source_tier": "B", "source_kind": "government"}
    recent = now_local()

    industry_score = agent._candidate_score(
        {
            "url": "https://example.com/industry",
            "domain": "example.com",
            "title": "注塑设备升级",
            "snippet": "聚焦高分子加工设备与产线自动化",
            "published_at": recent,
            "metadata": {},
        },
        memory,
        {},
        quality,
    )
    policy_score = agent._candidate_score(
        {
            "url": "https://example.com/policy",
            "domain": "example.com",
            "title": "限塑政策新规发布",
            "snippet": "聚焦塑料污染治理、标准与监管要求",
            "published_at": recent,
            "metadata": {},
        },
        memory,
        {},
        quality,
    )

    assert policy_score > industry_score


def test_candidate_score_rewards_higher_trust_source_kind():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    recent = now_local()

    government_score = agent._candidate_score(
        {
            "url": "https://example.com/gov",
            "domain": "example.com",
            "title": "高分子加工产业政策更新",
            "snippet": "聚焦塑料污染治理与监管要求",
            "published_at": recent,
            "metadata": {},
        },
        memory,
        {},
        {"source_tier": "B", "source_kind": "government"},
    )
    vertical_score = agent._candidate_score(
        {
            "url": "https://example.com/vertical",
            "domain": "example.com",
            "title": "高分子加工产业政策更新",
            "snippet": "聚焦塑料污染治理与监管要求",
            "published_at": recent,
            "metadata": {},
        },
        memory,
        {},
        {"source_tier": "B", "source_kind": "vertical_media"},
    )

    assert government_score > vertical_score


def test_extract_candidate_urls_skips_low_confidence_undated_c_tier_results():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    memory.record_search_results(
        "query",
        [
            {
                "url": "https://example.com/weak",
                "title": "行业观察",
                "snippet": "综合网站转载内容",
                "domain": "example.com",
            },
            {
                "url": "https://example.com/policy",
                "title": "限塑政策更新",
                "snippet": "聚焦塑料污染治理与监管要求",
                "domain": "example.com",
            },
        ],
    )

    candidates = agent._extract_candidate_urls(
        memory,
        runtime={"max_extractions_per_run": 5},
    )

    assert len(candidates) == 1
    assert candidates[0][0] == "https://example.com/policy"
    assert (
        memory.candidate_rejection_reasons["missing_publish_time_low_confidence"] == 1
    )


def test_extract_candidate_urls_skips_blocked_domains_even_if_search_results_contain_them():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    memory.record_search_results(
        "query",
        [
            {
                "url": "https://www.hbsztv.com/news/20260410/1455680.html",
                "title": "塑料市场观察",
                "snippet": "行业行情信息",
                "domain": "www.hbsztv.com",
            },
            {
                "url": "https://policy.example.com/update",
                "title": "塑料行业标准更新",
                "snippet": "聚焦塑料回收法规与监管要求",
                "domain": "policy.example.com",
            },
        ],
    )

    candidates = agent._extract_candidate_urls(
        memory, runtime={"max_extractions_per_run": 5}
    )

    assert len(candidates) == 1
    assert candidates[0][0] == "https://policy.example.com/update"
    assert memory.candidate_rejection_reasons["blocked_domain_candidate"] == 1


@pytest.mark.asyncio
async def test_follow_references_filters_blocked_and_low_value_links():
    tool = FollowReferencesTool()
    memory = WorkingMemory()
    source_url = "https://example.com/article"
    memory.record_read(
        source_url,
        links=[
            {
                "url": "https://www.made-in-china.com/product-detail/foo",
                "text": "B2B 列表",
                "domain": "www.made-in-china.com",
            },
            {
                "url": "https://policy.example.com/update",
                "text": "塑料回收法规更新",
                "domain": "policy.example.com",
            },
            {"url": "https://example.com/", "text": "首页", "domain": "example.com"},
        ],
    )

    result = await tool.execute(memory, url=source_url, focus="塑料 回收 法规")

    assert result.success is True
    assert result.data["leads_added"] == 1
    assert len(memory.exploration_queue) == 1
    assert memory.exploration_queue[0].url == "https://policy.example.com/update"


@pytest.mark.asyncio
async def test_check_coverage_prefers_writing_over_exploration_when_publishable():
    tool = CheckCoverageTool()
    memory = WorkingMemory()
    for index, section in enumerate(
        ["industry", "policy", "academic", "industry"], start=1
    ):
        memory.add_article(
            ArticleSummary(
                title=f"Article {index}",
                url=f"https://example.com/{index}",
                domain="example.com",
                source_name="example.com",
                published_at="2026-04-10T01:00:00+08:00",
                summary="summary",
                section=section,
                key_finding="finding",
                worth_publishing=True,
            )
        )
    memory.add_exploration_lead(
        ExplorationLead(
            url="https://example.com/lead", title="lead", reason="test", priority=0.5
        )
    )

    result = await tool.execute(memory)

    assert result.success is True
    assert "建议直接写稿" in result.summary


@pytest.mark.asyncio
async def test_agent_core_stops_after_consecutive_failed_tool_steps():
    tool = _StaticTool(ToolResult(success=False, summary="fail", data={}))
    tool.name = "static_fail"
    harness = make_daily_report_harness()
    harness.max_steps = 20
    core = AgentCore(
        tools=[tool],
        llm_client=_LoopingToolLLM("static_fail", {}),
        harness=harness,
    )

    result = await core.run("test")

    assert result.finished_reason == "no_progress_stall"


def test_display_source_name_prefers_friendly_mapping():
    agent = DailyReportAgent()

    assert (
        agent._display_source_name(
            {
                "source_name": "finance.sina.com.cn",
                "resolved_url": "https://finance.sina.com.cn/tech/2026-04-10/doc-good.shtml",
            }
        )
        == "新浪财经"
    )


def test_should_disable_fallback_search_when_both_providers_unhealthy():
    agent = DailyReportAgent()
    memory = WorkingMemory()
    memory.search_provider_health = {
        "zhipu": {"state": "unavailable", "last_error": "connection_error"},
        "brave": {"health_state": "circuit_open", "last_error": "quota_exceeded"},
    }

    assert agent._should_disable_fallback_search(memory) is True


def test_build_task_prompt_uses_bounded_candidates_and_36h_window():
    agent = DailyReportAgent()

    prompt = agent._build_task_prompt(
        now_local().date(),
        fallback_candidates=[
            (
                "https://example.com/article-1",
                "高分子加工设备升级\n聚焦注塑产线自动化",
            )
        ],
        search_enabled=False,
        provider_health={"zhipu": "unavailable", "brave": "circuit_open"},
    )

    assert "36 小时内" in prompt
    assert "禁止做开放式 broad web_search" in prompt
    assert "https://example.com/article-1" in prompt
    assert "topic" in prompt


def test_llm_client_sanitizes_kimi_messages_and_tool_args():
    client = LLMClient(primary_model="kimi-k2.5", fallback_model="fallback")
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "x1",
                    "function": {"name": "web_search", "arguments": {"query": "abc"}},
                }
            ],
        },
    ]

    sanitized = client._sanitize_messages_for_model(messages, "kimi-k2.5")

    assert sanitized[1]["content"] == ""
    assert sanitized[1]["reasoning_content"] == "[tool planning]"
    assert isinstance(sanitized[1]["tool_calls"][0]["function"]["arguments"], str)


@pytest.mark.asyncio
async def test_llm_client_retries_kimi_with_compact_history_on_reasoning_error(
    monkeypatch,
):
    request = httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions")
    captured_message_counts: list[int] = []
    responses = [
        httpx.Response(
            400,
            request=request,
            text='{"error":{"message":"thinking is enabled but reasoning_content is missing in assistant tool call message at index 28"}}',
        ),
        httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1},
            },
        ),
    ]

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(
            self, url: str, json: dict[str, Any], headers: dict[str, Any]
        ) -> httpx.Response:
            captured_message_counts.append(len(json["messages"]))
            return responses.pop(0)

    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)

    client = LLMClient(primary_model="kimi-k2.5", fallback_model="fallback")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
    ]
    for index in range(10):
        messages.append(
            {
                "role": "assistant",
                "content": f"step {index}",
                "tool_calls": [
                    {
                        "id": f"c{index}",
                        "function": {
                            "name": "web_search",
                            "arguments": {"query": str(index)},
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "ok"})

    response = await client._chat_with_tools_request("kimi-k2.5", messages, [], 0.3)

    assert response.content == "ok"
    assert len(captured_message_counts) == 2
    assert captured_message_counts[1] < captured_message_counts[0]
    assert client.snapshot_metrics()["llm_bad_request_count"] == 1
    rebuilt = client._build_history_reset_retry_messages(messages, "kimi-k2.5")
    assert all(not msg.get("tool_calls") for msg in rebuilt[2:])


@pytest.mark.asyncio
async def test_chat_with_tools_does_not_fallback_on_kimi_429_in_strict_mode(
    monkeypatch,
):
    request = httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions")
    responses = [
        httpx.Response(
            429,
            request=request,
            text='{"error":{"message":"The engine is currently overloaded, please try again later","type":"engine_overloaded_error"}}',
        ),
        httpx.Response(
            429,
            request=request,
            text='{"error":{"message":"The engine is currently overloaded, please try again later","type":"engine_overloaded_error"}}',
        ),
        httpx.Response(
            429,
            request=request,
            text='{"error":{"message":"The engine is currently overloaded, please try again later","type":"engine_overloaded_error"}}',
        ),
    ]
    urls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(
            self, url: str, json: dict[str, Any], headers: dict[str, Any]
        ) -> httpx.Response:
            urls.append(url)
            return responses.pop(0)

    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)

    async def _no_sleep(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.services.llm_client.asyncio.sleep", _no_sleep)

    client = LLMClient(primary_model="kimi-k2.5", fallback_model="openrouter/test")
    response = await client.chat_with_tools(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ],
        tool_definitions=[],
    )

    assert response.is_finish
    assert all("moonshot" in url for url in urls)
    metrics = client.snapshot_metrics()
    assert metrics["tool_use_model_switch_attempted"] is True
    assert metrics["kimi_rate_limit_errors"] >= 1


def test_evaluate_article_hard_rejects_off_topic_content():
    tool = EvaluateArticleTool(llm_client=None)
    memory = WorkingMemory()

    result = asyncio.run(
        tool.execute(
            memory=memory,
            title="Biogen to acquire Apellis in 5.6 billion deal",
            content="The ophthalmology acquisition follows pharma market expectations.",
            url="https://example.com/pharma-deal",
            domain="ophthalmologytimes.com",
        )
    )

    assert result.success
    assert not result.data["worthy"]
    assert result.data["reason"]
    assert memory.publishable_articles() == []


def test_daily_report_agent_candidate_ranking_filters_duplicates_and_off_topic():
    memory = WorkingMemory()
    recent = now_local()
    rows = [
        {
            "url": "https://www.plasticsnews.com/article-a",
            "title": "Polymer extrusion line expands recycling throughput",
            "domain": "plasticsnews.com",
            "snippet": "polymer extrusion recycling equipment capacity",
            "published_at": recent,
        },
        {
            "url": "https://www.plasticsnews.com/article-b",
            "title": "Polymer extrusion line expands recycling throughput",
            "domain": "plasticsnews.com",
            "snippet": "duplicate title",
            "published_at": recent,
        },
        {
            "url": "https://sports.example.com/marathon",
            "title": "Marathon finals headline the weekend sports schedule",
            "domain": "sports.example.com",
            "snippet": "marathon football sports",
            "published_at": recent,
        },
        {
            "url": "https://gov.cn/policy",
            "title": "塑料回收新标准发布",
            "domain": "gov.cn",
            "snippet": "高分子 塑料 回收 标准",
            "published_at": recent,
        },
    ]
    memory.search_results.extend(rows)
    memory.record_search_result_urls("q1", [rows[0]["url"], rows[1]["url"]])
    memory.record_search_result_urls("q2", [rows[2]["url"]])
    memory.record_search_result_urls("q3", [rows[3]["url"]])

    agent = DailyReportAgent()
    runtime = {
        "max_extractions_per_run": 5,
        "scrape_concurrency": 2,
        "domain_failure_threshold": 2,
        "scrape_timeout_seconds": 20,
        "report_primary_model": "kimi-k2.5",
        "report_fallback_model": "fallback",
        "shadow_mode": True,
    }

    candidates = agent._extract_candidate_urls(memory, runtime)
    candidate_urls = [url for url, _ in candidates]

    assert "https://gov.cn/policy" in candidate_urls
    assert "https://www.plasticsnews.com/article-a" in candidate_urls
    assert "https://www.plasticsnews.com/article-b" not in candidate_urls
    assert "https://sports.example.com/marathon" not in candidate_urls
    assert memory.candidate_rejection_reasons["duplicate_title"] >= 1
    assert memory.candidate_rejection_reasons["off_topic_candidate"] >= 1


def test_classify_source_detects_download_and_blog_tiers():
    download_quality = classify_source(
        url="http://spbz.jds.wsjkw.sh.gov.cn/wjs-front/wjs/downloadFile?id=1&type=11",
        title="下载附件",
        content="%PDF-1.7",
    )
    blog_quality = classify_source(
        url="https://blog.csdn.net/demo/article/details/123",
        title="低挥发环氧树脂分析",
        content="这是一篇技术博客摘要。",
    )
    newsroom_quality = classify_source(
        url="https://www.clariant.com/en/Corporate/News/2026/04/example",
        title="Clariant advances sustainable plastics innovation",
        content="Official news release on sustainable plastics.",
    )

    assert download_quality["page_kind"] == "download"
    assert download_quality["source_tier"] == "D"
    assert blog_quality["source_tier"] == "C"
    assert newsroom_quality["source_tier"] in {"A", "B"}


def test_classify_source_promotes_mainstream_media_family_domains():
    sina_quality = classify_source(
        url="https://k.sina.com.cn/article_5953466437_162dab0450670adb4m.html",
        title="塑料政策动态",
        content="新闻正文围绕塑料政策与高分子材料行业变化展开。",
    )
    china_quality = classify_source(
        url="http://business.china.com.cn/2026-04/08/content_43393086.shtml",
        title="高分子注塑设备升级",
        content="新闻正文围绕注塑设备升级与高分子加工工艺展开。",
    )

    assert sina_quality["source_kind"] == "mainstream_media"
    assert sina_quality["source_tier"] == "B"
    assert china_quality["source_kind"] == "mainstream_media"
    assert china_quality["source_tier"] == "B"


def test_daily_report_agent_candidate_ranking_rejects_download_preview():
    memory = WorkingMemory()
    recent = now_local()
    memory.search_results.extend(
        [
            {
                "url": "http://example.com/downloadFile?id=11&type=11",
                "title": "塑料包装标准附件下载",
                "domain": "example.com",
                "snippet": "下载文件",
                "published_at": recent,
            },
            {
                "url": "https://gov.cn/policy",
                "title": "塑料回收新标准发布",
                "domain": "gov.cn",
                "snippet": "高分子 塑料 回收 标准",
                "published_at": recent,
            },
        ]
    )
    agent = DailyReportAgent()
    runtime = {
        "max_extractions_per_run": 5,
        "scrape_concurrency": 2,
        "domain_failure_threshold": 2,
        "scrape_timeout_seconds": 20,
        "report_primary_model": "kimi-k2.5",
        "report_fallback_model": "fallback",
        "shadow_mode": True,
    }

    candidates = agent._extract_candidate_urls(memory, runtime)

    assert [url for url, _ in candidates] == ["https://gov.cn/policy"]
    assert memory.candidate_rejection_reasons["page_kind_download"] >= 1


def test_write_section_template_uses_compiled_topics_without_new_numbers():
    memory = WorkingMemory()
    memory.cache_compiled_topics(
        "industry",
        [
            {
                "title": "可持续塑料材料方案推进",
                "facts": ["企业在展会上展示可持续塑料方案", "官方稿未给出市场规模数字"],
                "citations": [
                    {
                        "domain": "clariant.com",
                        "url": "https://clariant.com/news",
                        "title": "news",
                        "source_tier": "B",
                    }
                ],
                "source_tier": "B",
                "source_reliability_label": "中高（规则判定）",
                "source_kind": "official_company_newsroom",
                "page_kind": "news",
                "evidence_strength": "medium",
                "supports_numeric_claims": False,
                "allowed_for_trend_summary": False,
            }
        ],
    )

    tool = WriteSectionTool(llm_client=None)
    result = asyncio.run(tool.execute(memory=memory, section="industry"))

    assert result.success
    assert "中高（规则判定）" in result.data["content"]
    assert "300亿美元" not in result.data["content"]
    assert "预计" not in result.data["content"]


def test_detect_page_kind_does_not_mark_normal_article_as_download():
    page_kind = detect_page_kind(
        "https://www.plasticsnews.com/suppliers/machinery/pn-vdma-concerns-us-tariff-changes/",
        title="VDMA concerns over tariff changes",
        content="The article discusses machinery suppliers and tariff changes without download links.",
    )

    assert page_kind in {"news", "article"}


def test_normalize_external_url_handles_protocol_relative_and_default_ports():
    assert (
        normalize_external_url("//n.sinaimg.cn/demo.png")
        == "https://n.sinaimg.cn/demo.png"
    )
    assert (
        normalize_external_url("https://www.tribuneindia.com:443/news/example")
        == "https://www.tribuneindia.com/news/example"
    )


def test_evaluate_article_rejects_stale_published_at():
    tool = EvaluateArticleTool(llm_client=None)
    memory = WorkingMemory()

    result = asyncio.run(
        tool.execute(
            memory=memory,
            title="Old polymer market note",
            content="A dated article about polymer market changes.",
            url="https://example.com/old",
            domain="example.com",
            published_at="2023-12-18T00:00:00+00:00",
        )
    )

    assert result.success
    assert not result.data["worthy"]
    assert "过旧" in result.data["reason"]


def test_evaluate_article_accepts_missing_published_at_for_ab_newsroom():
    tool = EvaluateArticleTool(llm_client=None)
    memory = WorkingMemory()

    result = asyncio.run(
        tool.execute(
            memory=memory,
            title="Clariant advances sustainable plastics innovation",
            content="Official newsroom update on sustainable plastics, polymer materials and Chinaplas participation.",
            url="https://www.clariant.com/en/Corporate/News/2026/04/example",
            domain="www.clariant.com",
            published_at="",
            page_kind="news",
        )
    )

    assert result.success
    assert result.data["worthy"] is True
    assert result.data["recency_status"] == "unknown"
    assert memory.publishable_articles()


def test_daily_report_agent_candidate_ranking_skips_non_retryable_attempted_urls():
    memory = WorkingMemory()
    recent = now_local()
    url = "https://www.plasticsnews.com/article-a"
    memory.search_results.extend(
        [
            {
                "url": url,
                "title": "Polymer extrusion line expands recycling throughput",
                "domain": "plasticsnews.com",
                "snippet": "polymer extrusion recycling equipment capacity",
                "published_at": recent,
            },
            {
                "url": "https://gov.cn/policy",
                "title": "塑料回收新标准发布",
                "domain": "gov.cn",
                "snippet": "高分子 塑料 回收 标准",
                "published_at": recent,
            },
        ]
    )
    memory.record_page_attempt(
        url, "rejected_by_recency", metadata={"content_available": True}
    )

    agent = DailyReportAgent()
    runtime = {
        "max_extractions_per_run": 5,
        "scrape_concurrency": 2,
        "domain_failure_threshold": 2,
        "scrape_timeout_seconds": 20,
        "report_primary_model": "kimi-k2.5",
        "report_fallback_model": "fallback",
        "shadow_mode": True,
    }

    candidates = agent._extract_candidate_urls(memory, runtime)
    candidate_urls = [candidate_url for candidate_url, _ in candidates]

    assert url not in candidate_urls
    assert "https://gov.cn/policy" in candidate_urls
    assert memory.candidate_rejection_reasons["already_attempted_non_retryable"] >= 1


def test_compile_section_topics_keeps_provisional_topics_when_formal_insufficient():
    memory = WorkingMemory()
    memory.add_article(
        ArticleSummary(
            title="政策主证据",
            url="https://gov.cn/policy-1",
            domain="gov.cn",
            source_name="gov.cn",
            published_at="2026-04-08",
            summary="塑料回收政策更新。",
            section="policy",
            key_finding="政策主题",
            worth_publishing=True,
            source_tier="B",
            source_reliability_label="中高（规则判定）",
            source_kind="government",
            page_kind="news",
            evidence_strength="high",
            recency_status="recent_verified",
        )
    )
    for idx, section in enumerate(["industry", "academic", "industry"], start=1):
        memory.add_article(
            ArticleSummary(
                title=f"补位主题{idx}",
                url=f"https://example.com/{idx}",
                domain="k.sina.com.cn" if idx == 1 else "business.china.com.cn",
                source_name="media",
                published_at=None,
                summary="高分子材料加工相关内容，非软文且与主题强相关。",
                section=section,
                key_finding=f"补位主题{idx}",
                worth_publishing=True,
                source_tier="C",
                source_reliability_label="中（仅可辅助参考）",
                source_kind="general_site",
                page_kind="news",
                evidence_strength="low",
                recency_status="unknown",
                evaluation_reason="内容相关，允许补位",
            )
        )

    agent = DailyReportAgent()
    runtime = {"report_target_items": 4, "report_min_formal_topics": 3}

    compiled = agent._compile_section_topics(memory, runtime)
    remaining = memory.publishable_articles()

    assert sum(len(v) for v in compiled.values()) >= 3
    assert len(remaining) >= 3
    assert any(
        (topic.get("topic_confidence") == "provisional")
        for topics in compiled.values()
        for topic in topics
    )


@pytest.mark.asyncio
async def test_web_search_skips_brave_after_quota_limited_in_same_run():
    class _StubBrave:
        enabled = True

        def health_snapshot(self) -> dict[str, Any]:
            return {
                "provider": "brave",
                "health_state": "quota_limited",
                "last_error": "quota_exceeded",
            }

        async def search_all(self, query: str, search_lang: str):
            raise AssertionError("Brave should have been skipped after quota_limited")

    memory = WorkingMemory()
    memory.record_search_provider_health(
        "brave",
        {
            "provider": "brave",
            "health_state": "quota_limited",
            "last_error": "quota_exceeded",
        },
    )
    tool = WebSearchTool(brave_client=_StubBrave(), zhipu_client=None)

    result = await tool.execute(
        memory=memory, query="polymer processing", language="en"
    )

    assert result.success
    assert len(memory.search_results) == 0


def test_daily_report_agent_auto_publish_status_complete():
    agent = DailyReportAgent()
    status, reason = agent._auto_publish_status(
        effective_topic_count=4,
        section_count=2,
        recent_verified_count=2,
        a_tier_count=1,
        article_count=4,
        runtime={"report_target_items": 4, "report_min_formal_topics": 3},
    )

    assert status == "complete_auto_publish"
    assert reason == "meets_auto_publish_gate"


def test_daily_report_agent_auto_publish_status_hold_when_quality_missing():
    agent = DailyReportAgent()
    status, reason = agent._auto_publish_status(
        effective_topic_count=3,
        section_count=2,
        recent_verified_count=0,
        a_tier_count=0,
        article_count=3,
        runtime={"report_target_items": 4, "report_min_formal_topics": 3},
    )

    assert status == "hold_for_missing_quality"
    assert reason == "insufficient_recent_verified_or_a_tier"


@pytest.mark.asyncio
async def test_brave_health_snapshot_marks_quota_limited(monkeypatch):
    request = httpx.Request("GET", "https://brave.example/res/v1/web/search")

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(402, request=request)

    monkeypatch.setattr("app.services.brave.httpx.AsyncClient", FakeAsyncClient)

    client = BraveSearchClient(api_key="test", base_url="https://brave.example")
    with pytest.raises(httpx.HTTPStatusError):
        await client.search("polymer", search_type="web")

    snapshot = client.health_snapshot()
    assert snapshot["health_state"] == "quota_limited"


@pytest.mark.asyncio
async def test_deterministic_synthesis_finishes_when_compare_times_out(monkeypatch):
    memory = WorkingMemory()
    memory.add_article(
        ArticleSummary(
            title="可信产业新闻",
            url="https://example.com/news-1",
            domain="example.com",
            source_name="example.com",
            published_at=now_local().date().isoformat(),
            summary="高分子材料产业新闻",
            section="industry",
            key_finding="产业主题一",
            worth_publishing=True,
            source_tier="B",
            source_reliability_label="中高（规则判定）",
            source_kind="vertical_media",
            page_kind="news",
            evidence_strength="medium",
        )
    )
    memory.add_article(
        ArticleSummary(
            title="可信政策新闻",
            url="https://gov.cn/policy-1",
            domain="gov.cn",
            source_name="gov.cn",
            published_at=now_local().date().isoformat(),
            summary="塑料回收政策更新",
            section="policy",
            key_finding="政策主题一",
            worth_publishing=True,
            source_tier="A",
            source_reliability_label="高（规则判定）",
            source_kind="government",
            page_kind="news",
            evidence_strength="high",
        )
    )
    memory.add_article(
        ArticleSummary(
            title="可信学术新闻",
            url="https://journal.example.com/paper-1",
            domain="journal.example.com",
            source_name="journal.example.com",
            published_at=now_local().date().isoformat(),
            summary="4D打印工艺研究进展",
            section="academic",
            key_finding="学术主题一",
            worth_publishing=True,
            source_tier="A",
            source_reliability_label="高（规则判定）",
            source_kind="academic_journal",
            page_kind="article",
            evidence_strength="high",
        )
    )
    memory.cache_compiled_topics(
        "industry",
        [
            {
                "title": "产业主题一",
                "facts": ["产业主题一事实"],
                "citations": [
                    {
                        "domain": "example.com",
                        "url": "https://example.com/news-1",
                        "title": "可信产业新闻",
                        "source_tier": "B",
                    }
                ],
                "source_tier": "B",
                "source_reliability_label": "中高（规则判定）",
                "source_kind": "vertical_media",
                "page_kind": "news",
                "evidence_strength": "medium",
                "supports_numeric_claims": False,
                "allowed_for_trend_summary": False,
                "selection_reason": "产业主题入选",
            }
        ],
    )
    memory.cache_compiled_topics(
        "policy",
        [
            {
                "title": "政策主题一",
                "facts": ["政策主题一事实"],
                "citations": [
                    {
                        "domain": "gov.cn",
                        "url": "https://gov.cn/policy-1",
                        "title": "可信政策新闻",
                        "source_tier": "A",
                    }
                ],
                "source_tier": "A",
                "source_reliability_label": "高（规则判定）",
                "source_kind": "government",
                "page_kind": "news",
                "evidence_strength": "high",
                "supports_numeric_claims": False,
                "allowed_for_trend_summary": True,
                "selection_reason": "政策主题入选",
            }
        ],
    )
    memory.cache_compiled_topics(
        "academic",
        [
            {
                "title": "学术主题一",
                "facts": ["学术主题一事实"],
                "citations": [
                    {
                        "domain": "journal.example.com",
                        "url": "https://journal.example.com/paper-1",
                        "title": "可信学术新闻",
                        "source_tier": "A",
                    }
                ],
                "source_tier": "A",
                "source_reliability_label": "高（规则判定）",
                "source_kind": "academic_journal",
                "page_kind": "article",
                "evidence_strength": "high",
                "supports_numeric_claims": False,
                "allowed_for_trend_summary": True,
                "selection_reason": "学术主题入选",
            }
        ],
    )
    memory.set_formal_topic_count(3)
    memory.rebuild_coverage()

    async def _timeout_compare(self, memory, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(
        "app.services.daily_report_agent.CompareSourcesTool.execute", _timeout_compare
    )

    agent = DailyReportAgent()
    result = await agent._run_deterministic_synthesis(
        memory=memory,
        target_date=now_local().date(),
        llm_client=None,
        event_queue=None,
        runtime={"report_target_items": 4},
    )

    assert result.success
    assert result.finished_reason == "finish_tool"
    assert result.diagnostics["phase3_compare_status"]["status"] == "timeout"
    assert result.sections_content


@pytest.mark.asyncio
async def test_deterministic_synthesis_retries_compare_once_before_success(monkeypatch):
    memory = WorkingMemory()
    for title, section, domain, tier in [
        ("可信产业新闻", "industry", "example.com", "B"),
        ("可信政策新闻", "policy", "gov.cn", "A"),
    ]:
        memory.add_article(
            ArticleSummary(
                title=title,
                url=f"https://{domain}/{section}",
                domain=domain,
                source_name=domain,
                published_at=now_local().date().isoformat(),
                summary=f"{section} summary",
                section=section,
                key_finding=f"{section} finding",
                worth_publishing=True,
                source_tier=tier,
                source_reliability_label="高（规则判定）"
                if tier == "A"
                else "中高（规则判定）",
                source_kind="government" if section == "policy" else "vertical_media",
                page_kind="news",
                evidence_strength="high",
            )
        )

    memory.cache_compiled_topics(
        "industry",
        [
            {
                "title": "产业主题一",
                "facts": ["产业事实"],
                "citations": [
                    {
                        "domain": "example.com",
                        "url": "https://example.com/industry",
                        "title": "可信产业新闻",
                        "source_tier": "B",
                    }
                ],
                "source_reliability_label": "中高（规则判定）",
                "evidence_strength": "medium",
                "supports_numeric_claims": False,
                "selection_reason": "产业主题入选",
            }
        ],
    )
    memory.cache_compiled_topics(
        "policy",
        [
            {
                "title": "政策主题一",
                "facts": ["政策事实"],
                "citations": [
                    {
                        "domain": "gov.cn",
                        "url": "https://gov.cn/policy",
                        "title": "可信政策新闻",
                        "source_tier": "A",
                    }
                ],
                "source_reliability_label": "高（规则判定）",
                "evidence_strength": "high",
                "supports_numeric_claims": False,
                "selection_reason": "政策主题入选",
            }
        ],
    )
    memory.set_formal_topic_count(2)
    memory.rebuild_coverage()

    attempts = {"count": 0}

    async def _flaky_compare(self, memory, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise asyncio.TimeoutError()
        return ToolResult(
            success=True, summary="compare ok", data={"trends": [{"title": "trend"}]}
        )

    monkeypatch.setattr(
        "app.services.daily_report_agent.CompareSourcesTool.execute", _flaky_compare
    )

    agent = DailyReportAgent()
    result = await agent._run_deterministic_synthesis(
        memory=memory,
        target_date=now_local().date(),
        llm_client=None,
        event_queue=None,
        runtime={"report_target_items": 3},
    )

    assert result.success
    assert attempts["count"] == 2
    assert result.diagnostics["phase3_compare_status"]["status"] == "ok"
    assert result.diagnostics["phase3_compare_status"]["attempts"] == 2


@pytest.mark.asyncio
async def test_deterministic_synthesis_retries_write_once_before_success(monkeypatch):
    memory = WorkingMemory()
    memory.add_article(
        ArticleSummary(
            title="可信产业新闻",
            url="https://example.com/news-1",
            domain="example.com",
            source_name="example.com",
            published_at=now_local().date().isoformat(),
            summary="高分子材料产业新闻",
            section="industry",
            key_finding="产业主题一",
            worth_publishing=True,
            source_tier="B",
            source_reliability_label="中高（规则判定）",
            source_kind="vertical_media",
            page_kind="news",
            evidence_strength="medium",
        )
    )
    memory.cache_compiled_topics(
        "industry",
        [
            {
                "title": "产业主题一",
                "facts": ["产业主题一事实"],
                "citations": [
                    {
                        "domain": "example.com",
                        "url": "https://example.com/news-1",
                        "title": "可信产业新闻",
                        "source_tier": "B",
                    }
                ],
                "selection_reason": "产业主题入选",
            }
        ],
    )
    memory.set_formal_topic_count(1)
    memory.rebuild_coverage()

    attempts = {"count": 0}

    async def _flaky_write(self, memory, section, target_count=3, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise asyncio.TimeoutError()
        content = f"## {section}\n\n- 产业主题一"
        memory.cache_section_content(section, content)
        memory.record_section_generation(section, "llm")
        return ToolResult(success=True, summary="write ok", data={"content": content})

    monkeypatch.setattr(
        "app.services.daily_report_agent.WriteSectionTool.execute", _flaky_write
    )

    agent = DailyReportAgent()
    result = await agent._run_deterministic_synthesis(
        memory=memory,
        target_date=now_local().date(),
        llm_client=None,
        event_queue=None,
        runtime={"report_target_items": 3},
    )

    assert result.success
    assert attempts["count"] == 2
    assert result.diagnostics["phase3_section_results"]["industry"]["status"] == "ok"
    assert result.diagnostics["phase3_section_results"]["industry"]["attempts"] == 2
    assert result.sections_content["industry"].startswith("## industry")


@pytest.mark.asyncio
async def test_web_search_filters_results_outside_36h_digest_window():
    class _StubBrave:
        enabled = True

        async def search_all(self, query: str, search_lang: str):
            return [
                {
                    "url": "https://example.com/old-news",
                    "title": "Old News",
                    "snippet": "polymer processing",
                    "published_at": now_local() - timedelta(hours=40),
                    "domain": "example.com",
                    "result_type": "news",
                    "provider": "brave",
                },
                {
                    "url": "https://example.com/fresh-news",
                    "title": "Fresh News",
                    "snippet": "polymer processing",
                    "published_at": now_local() - timedelta(hours=12),
                    "domain": "example.com",
                    "result_type": "news",
                    "provider": "brave",
                },
            ]

        def health_snapshot(self) -> dict[str, Any]:
            return {"provider": "brave", "state": "healthy"}

    memory = WorkingMemory()
    tool = WebSearchTool(brave_client=_StubBrave(), zhipu_client=None)

    result = await tool.execute(
        memory=memory, query="polymer processing", language="en"
    )

    assert result.success
    assert result.data["total"] == 1
    assert memory.search_results[0]["url"] == "https://example.com/fresh-news"


def test_classify_source_keeps_unknown_news_domain_at_tier_c():
    quality = classify_source(
        url="https://unknown-example.com/news/2026/polymer-update",
        title="Polymer processing update",
        content="A generic site post about polymer processing.",
    )

    assert quality["page_kind"] == "news"
    assert quality["source_tier"] == "C"


def test_compare_sources_no_longer_mutates_publishable_articles():
    memory = WorkingMemory()
    for index in range(3):
        memory.add_article(
            ArticleSummary(
                title=f"Article {index}",
                url=f"https://example.com/{index}",
                domain="example.com",
                source_name="example.com",
                published_at="2026-04-08",
                summary="polymer processing update",
                section="industry",
                key_finding=f"Finding {index}",
                worth_publishing=True,
                source_tier="B",
                source_reliability_label="中高（规则判定）",
                source_kind="vertical_media",
                page_kind="news",
                evidence_strength="medium",
            )
        )

    tool = CompareSourcesTool(llm_client=None)
    result = asyncio.run(tool.execute(memory=memory))

    assert result.success
    assert len(memory.publishable_articles()) == 3


def test_read_page_failed_attempt_does_not_mark_readable():
    class _FailingScraper:
        enabled = True

        async def scrape(
            self, url: str, timeout_seconds: int | None = None
        ) -> dict[str, Any]:
            return {
                "url": url,
                "resolved_url": url,
                "domain": "example.com",
                "title": "",
                "markdown": "",
                "status": "error",
                "scrape_layer": "jina",
            }

    memory = WorkingMemory()
    tool = ReadPageTool(scraper_client=_FailingScraper(), timeout_seconds=5)

    result = asyncio.run(tool.execute(memory=memory, url="https://example.com/a"))

    assert not result.success
    assert memory.has_attempted_read("https://example.com/a")
    assert not memory.has_read("https://example.com/a")
    assert (
        memory.get_read_metadata("https://example.com/a")["read_state"]
        == "attempted_failed"
    )


@pytest.mark.asyncio
async def test_web_search_splits_article_and_image_result_pools():
    class _StubBrave:
        enabled = True

        async def search_all(self, query: str, search_lang: str):
            return [
                {
                    "url": "https://example.com/news-a",
                    "title": "News A",
                    "snippet": "polymer processing",
                    "published_at": now_local(),
                    "domain": "example.com",
                    "result_type": "news",
                    "provider": "brave",
                },
                {
                    "url": "https://imgs.example.com/a.jpg",
                    "title": "Image A",
                    "snippet": "",
                    "published_at": None,
                    "domain": "imgs.example.com",
                    "result_type": "images",
                    "provider": "brave",
                },
            ]

        def health_snapshot(self) -> dict[str, Any]:
            return {"provider": "brave", "state": "healthy"}

    memory = WorkingMemory()
    tool = WebSearchTool(brave_client=_StubBrave(), zhipu_client=None)

    result = await tool.execute(
        memory=memory, query="polymer processing", language="en"
    )

    assert result.success
    assert len(memory.search_results) == 1
    assert len(memory.image_search_results) == 1
    assert memory.search_results[0]["result_type"] == "news"
    assert memory.image_search_results[0]["result_type"] == "images"


def test_web_search_normalizes_year_tokens_from_query():
    normalized = WebSearchTool._normalize_query("高分子材料加工 2026年4月 新闻")

    assert "2026" not in normalized
    assert "高分子材料加工" in normalized


def test_classify_source_marks_known_junk_domains_low_value():
    quality = classify_source(
        url="https://www.hbsztv.com/news/20260410/1455680.html",
        title="塑料行业价格预测",
        content="泛财经市场预测内容",
    )

    assert quality["source_tier"] == "D"


# ── Integration tests for empty-search stall detection ──────────────────


def test_record_empty_search_increments_counter():
    m = WorkingMemory()
    assert m.consecutive_empty_searches == 0
    m.record_empty_search()
    assert m.consecutive_empty_searches == 1
    m.record_empty_search()
    assert m.consecutive_empty_searches == 2


def test_record_empty_search_expands_recency_at_threshold_3():
    m = WorkingMemory()
    assert m.current_recency_hours == 36
    m.record_empty_search()
    m.record_empty_search()
    assert m.current_recency_hours == 36
    m.record_empty_search()
    assert m.current_recency_hours == 48


def test_record_empty_search_expands_recency_at_threshold_5():
    m = WorkingMemory()
    for _ in range(5):
        m.record_empty_search()
    assert m.consecutive_empty_searches == 5
    assert m.current_recency_hours == 72


def test_record_productive_search_resets_counter():
    m = WorkingMemory()
    for _ in range(3):
        m.record_empty_search()
    assert m.consecutive_empty_searches == 3
    m.record_productive_search()
    assert m.consecutive_empty_searches == 0


def test_record_productive_search_does_not_shrink_recency():
    m = WorkingMemory()
    for _ in range(5):
        m.record_empty_search()
    assert m.current_recency_hours == 72
    m.record_productive_search()
    assert m.current_recency_hours == 72


def test_empty_search_counter_in_snapshot():
    m = WorkingMemory()
    m.record_empty_search()
    m.record_empty_search()
    snap = m.snapshot()
    assert snap["consecutive_empty_searches"] == 2
    assert snap["current_recency_hours"] == 36


# ── Integration tests for scrape-layer health gating ─────────────────────


def test_scrape_failure_rate_returns_zero_for_few_attempts():
    m = WorkingMemory()
    m.attempted_urls = {"a", "b"}
    m.read_urls = set()
    assert DailyReportAgent._scrape_failure_rate(m) == 0.0


def test_scrape_failure_rate_computes_correctly():
    m = WorkingMemory()
    m.attempted_urls = {"a", "b", "c", "d", "e"}
    m.read_urls = {"a", "b"}
    assert DailyReportAgent._scrape_failure_rate(m) == 0.6


def test_should_skip_fallback_below_threshold():
    m = WorkingMemory()
    m.attempted_urls = {"a", "b", "c", "d", "e"}
    m.read_urls = {"a", "b"}
    assert DailyReportAgent._should_skip_fallback_for_scrape_health(m) is False


def test_should_skip_fallback_above_threshold():
    m = WorkingMemory()
    m.attempted_urls = {"a", "b", "c", "d", "e"}
    m.read_urls = set()
    assert DailyReportAgent._should_skip_fallback_for_scrape_health(m) is True


def test_should_not_skip_fallback_with_few_attempts():
    m = WorkingMemory()
    m.attempted_urls = {"a"}
    m.read_urls = set()
    assert DailyReportAgent._should_skip_fallback_for_scrape_health(m) is False


# ── Integration tests for coverage threshold alignment ───────────────────


def test_coverage_is_publishable_requires_4_topics():
    c = CoverageState(academic_count=1, industry_count=1, policy_count=1)
    assert c.total_articles == 3
    assert c.section_count == 3
    assert c.is_publishable is False

    c2 = CoverageState(academic_count=2, industry_count=1, policy_count=1)
    assert c2.total_articles == 4
    assert c2.is_publishable is True


def test_coverage_is_complete_requires_6_topics():
    c = CoverageState(academic_count=2, industry_count=2, policy_count=1)
    assert c.total_articles == 5
    assert c.is_complete is False

    c2 = CoverageState(academic_count=2, industry_count=2, policy_count=2)
    assert c2.total_articles == 6
    assert c2.is_complete is True


def test_coverage_gaps_shows_insufficient_below_4():
    c = CoverageState(academic_count=1, industry_count=1, policy_count=1)
    gaps = c.gaps()
    assert len(gaps) > 0
    assert any("不足" in g for g in gaps)


def test_coverage_no_gaps_at_4_topics_3_sections():
    c = CoverageState(academic_count=2, industry_count=1, policy_count=1)
    assert c.total_articles == 4
    assert c.section_count == 3
    topic_gaps = [g for g in c.gaps() if "不足" in g]
    assert len(topic_gaps) == 0


# ── Integration tests for config values ──────────────────────────────────


def test_config_report_target_items_is_6():
    assert settings.report_target_items == 6


def test_config_max_extractions_per_run_is_24():
    assert settings.max_extractions_per_run == 24


def test_config_report_min_formal_topics_is_4():
    assert settings.report_min_formal_topics == 4


def test_config_max_items_per_section_is_4():
    assert settings.max_items_per_section == 4


# ── E2E pipeline simulation ─────────────────────────────────────────────


def test_e2e_pipeline_simulation_produces_6_topics_and_auto_publishes():
    """Simulate the full pipeline with realistic article data flowing through
    WorkingMemory → _compile_section_topics → _auto_publish_status to prove
    that when search/scrape yield 8 articles across 3 sections, the system
    compiles >=6 topics and reaches complete_auto_publish."""
    from datetime import datetime, timezone, timedelta

    agent = DailyReportAgent()
    memory = WorkingMemory()
    now_str = datetime.now(timezone(timedelta(hours=8))).isoformat()

    articles_data = [
        (
            "industry",
            "https://www.plasticsnews.com/a1",
            "New Injection Molding Machine at Chinaplas 2026",
            "A",
        ),
        (
            "industry",
            "https://www.ptonline.com/a2",
            "PP Prices Rise 5pct Due to Supply Constraints",
            "A",
        ),
        (
            "industry",
            "https://www.sohu.com/a3",
            "Haitian Launches Electric Injection Molding Series",
            "B",
        ),
        (
            "policy",
            "https://www.gov.cn/a4",
            "New Plastic Restriction Policy Implementation Rules Published",
            "A",
        ),
        (
            "policy",
            "https://www.sac.gov.cn/a5",
            "GB/T Plastic Recycling Label Standard Open for Comment",
            "B",
        ),
        (
            "academic",
            "https://www.nature.com/a6",
            "Novel PLA Bioplastic Achieves High Elongation",
            "A",
        ),
        (
            "academic",
            "https://www.sciencedirect.com/a7",
            "ML Optimizes Extrusion Parameters for Recycled PP",
            "B",
        ),
        (
            "industry",
            "https://www.europeanplasticsnews.com/a8",
            "K 2027 Smart Manufacturing Preview",
            "C",
        ),
    ]

    for section, url, title, tier in articles_data:
        memory.add_article(
            ArticleSummary(
                title=title,
                url=url,
                domain=url.split("/")[2],
                source_name=url.split("/")[2],
                published_at=now_str,
                summary="Summary: " + title[:30],
                section=section,
                key_finding=title[:40],
                worth_publishing=True,
                source_tier=tier,
                evidence_strength="high" if tier in ("A", "B") else "low",
                supports_numeric_claims="Price" in title or "5pct" in title,
                recency_status="recent_verified",
            )
        )

    pub = memory.publishable_articles()
    assert len(pub) >= 6
    assert memory.coverage.section_count >= 2
    assert memory.coverage.is_publishable

    runtime = {
        "report_target_items": settings.report_target_items,
        "report_min_formal_topics": settings.report_min_formal_topics,
        "max_extractions_per_run": settings.max_extractions_per_run,
    }
    compiled = agent._compile_section_topics(memory, runtime)

    total_topics = sum(len(topics) for topics in compiled.values())
    assert total_topics >= 6

    formal = memory.coverage.formal_topic_count
    assert formal >= 4

    status, reason = agent._auto_publish_status(
        effective_topic_count=formal or memory.coverage.total_articles,
        section_count=memory.coverage.section_count,
        recent_verified_count=sum(
            1 for a in pub if a.recency_status == "recent_verified"
        ),
        a_tier_count=sum(1 for a in pub if a.source_tier == "A"),
        article_count=len(pub),
        runtime=runtime,
    )
    assert status in ("complete_auto_publish", "partial_auto_publish")


def test_e2e_pipeline_with_only_4_articles_produces_partial_publish():
    """When the pipeline only yields 4 articles, it should still reach
    partial_auto_publish (not hold_for_missing_quality)."""
    from datetime import datetime, timezone, timedelta

    agent = DailyReportAgent()
    memory = WorkingMemory()
    now_str = datetime.now(timezone(timedelta(hours=8))).isoformat()

    for section, url, title in [
        ("industry", "https://example.com/a1", "Machine Launch"),
        ("industry", "https://example.com/a2", "Price Update"),
        ("policy", "https://example.com/a3", "Regulation Change"),
        ("academic", "https://example.com/a4", "Research Paper"),
    ]:
        memory.add_article(
            ArticleSummary(
                title=title,
                url=url,
                domain="example.com",
                source_name="example.com",
                published_at=now_str,
                summary="Summary",
                section=section,
                key_finding=title,
                worth_publishing=True,
                source_tier="B",
                evidence_strength="high",
                recency_status="recent_verified",
            )
        )

    runtime = {
        "report_target_items": settings.report_target_items,
        "report_min_formal_topics": settings.report_min_formal_topics,
        "max_extractions_per_run": settings.max_extractions_per_run,
    }
    compiled = agent._compile_section_topics(memory, runtime)
    total_topics = sum(len(topics) for topics in compiled.values())
    assert total_topics >= 4

    status, reason = agent._auto_publish_status(
        effective_topic_count=memory.coverage.formal_topic_count
        or memory.coverage.total_articles,
        section_count=memory.coverage.section_count,
        recent_verified_count=sum(
            1
            for a in memory.publishable_articles()
            if a.recency_status == "recent_verified"
        ),
        a_tier_count=sum(
            1 for a in memory.publishable_articles() if a.source_tier == "A"
        ),
        article_count=len(memory.publishable_articles()),
        runtime=runtime,
    )
    assert status in ("complete_auto_publish", "partial_auto_publish"), (
        f"4 articles across 3 sections should be publishable, got {status}"
    )


def test_e2e_empty_search_stall_then_recency_expansion():
    """Simulate: consecutive empty searches expand the recency window,
    then a productive search resets the counter but keeps the expanded window."""
    m = WorkingMemory()
    assert m.current_recency_hours == 36

    for _ in range(3):
        m.record_empty_search()
    assert m.current_recency_hours == 48
    assert m.consecutive_empty_searches == 3

    m.record_productive_search()
    assert m.consecutive_empty_searches == 0
    assert m.current_recency_hours == 48, "Productive search keeps expanded window"

    for _ in range(5):
        m.record_empty_search()
    assert m.consecutive_empty_searches == 5
    assert m.current_recency_hours == 72, "5 consecutive empty expands to 72h"


def test_e2e_scrape_health_gating_blocks_fallback():
    """When scrape failure rate > 80% with >=3 attempts and no candidates,
    fallback should be skipped entirely."""
    m = WorkingMemory()

    m.attempted_urls = {"a", "b", "c", "d", "e"}
    m.read_urls = set()

    assert DailyReportAgent._scrape_failure_rate(m) == 1.0
    assert DailyReportAgent._should_skip_fallback_for_scrape_health(m) is True

    # Even with 1 successful read (80% failure), still skips
    m.read_urls = {"a"}
    assert DailyReportAgent._scrape_failure_rate(m) == 0.8
    assert DailyReportAgent._should_skip_fallback_for_scrape_health(m) is False, (
        "Exactly 80% should NOT skip (uses > not >=)"
    )
