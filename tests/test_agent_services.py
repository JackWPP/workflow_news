from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.agent_core import AgentCore
from app.services.article_agent import ArticleAgent
from app.services.brave import BraveSearchClient
from app.services.harness import make_daily_report_harness
from app.services.llm_client import LLMResponse
from app.services.tools import Tool, ToolResult
from app.services.working_memory import ArticleSummary, WorkingMemory


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
        return ToolResult(success=True, summary="ok", data={"suitable": True, "reason": "ok"})


class _NoopLLM:
    async def chat_with_tools(self, messages, tool_definitions, temperature=0.3) -> LLMResponse:
        return LLMResponse(content="done", is_finish=True)

    def build_tool_result_message(self, tool_call_id: str, result_content: str) -> dict[str, Any]:
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

    core = AgentCore(tools=[], llm_client=_NoopLLM(), harness=make_daily_report_harness())
    core._enrich_articles_with_images(memory)
    article = memory.publishable_articles()[0]
    assert article.image_url == image_url
    assert article.has_image
