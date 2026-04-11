"""
research_agent.py — 研究型 Agent

取代 chat.py 中的 ChatService.build_answer()。

旧 ChatService：
  1. SQL LIKE 搜索本地 report_items
  2. 可选 Brave 搜 3 条外网
  3. 单次 LLM 补全

新 ResearchAgent：
  1. 先搜本地日报库（LocalCorpusSearchTool）
  2. 本地不够时自动搜外网（WebSearchTool）
  3. 深入阅读关键文章（ReadPageTool）
  4. 追踪引用（FollowReferencesTool）
  5. 多步综合分析，带引用输出
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services.agent_core import AgentCore, AgentResult
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.tools import (
    CompareSourcesTool,
    EvaluateArticleTool,
    FinishTool,
    FollowReferencesTool,
    ReadPageTool,
    Tool,
    ToolResult,
    WebSearchTool,
)
from app.services.working_memory import WorkingMemory

logger = logging.getLogger(__name__)


# ── System Prompt ─────────────────────────────────────────

RESEARCH_SYSTEM_PROMPT = """你是高分子材料加工领域的专业研究助手。用户会向你提问，你需要通过多步检索和分析来给出有深度的回答。

你的工作方式：
  1. 先用 search_local 搜索本地日报库（这里有我们过去整理的高质量文章）
  2. 如果本地库找到的内容不够，再用 web_search 搜索外网
  3. 发现重要文章后，用 read_page 深入阅读
  4. 如果文章中提到了重要引用，用 follow_references 追踪
  5. 综合所有发现，给出带引用的完整回答
  6. 用 finish 输出最终答案

回答质量要求：
  - 每个重要观点必须有来源引用
  - 如果本地库有相关内容，优先引用（避免重复外搜）
  - 诚实说明信息局限性
  - 专业但易懂，中文回答"""


# ── Local Corpus Search Tool ──────────────────────────────


class LocalCorpusSearchTool(Tool):
    """搜索本地日报库，找到过去整理的相关文章。"""

    name = "search_local"
    description = (
        "搜索本地日报库中的文章和报告。"
        "本地库包含过去几周整理的高质量高分子材料加工领域文章。"
        "优先在这里搜索，找到相关内容后可以不必外搜。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索词，支持中文和英文",
            },
            "days": {
                "type": "integer",
                "description": "搜索最近几天的内容（默认 30 天）",
            },
        },
        "required": ["query"],
    }

    def __init__(self, session: Any = None) -> None:
        self._session = session

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "").strip()
        days: int = int(kwargs.get("days", 30))

        if not query or self._session is None:
            return ToolResult(success=False, summary="搜索服务不可用", data={})

        try:
            from datetime import datetime, timedelta

            from sqlalchemy import or_, select

            from app.models import Article, Report, ReportItem

            since = datetime.utcnow() - timedelta(days=days)
            terms = [t.strip() for t in query.split() if t.strip()][:4]

            # 搜索 ReportItems
            conditions = []
            for term in terms:
                conditions.append(ReportItem.title.ilike(f"%{term}%"))
                conditions.append(ReportItem.summary.ilike(f"%{term}%"))

            stmt = (
                select(ReportItem)
                .join(Report, ReportItem.report_id == Report.id)
                .where(
                    or_(*conditions) if conditions else True,
                    Report.created_at >= since,
                )
                .order_by(Report.report_date.desc())
                .limit(6)
            )
            items = list(self._session.scalars(stmt).all())

            if not items:
                return ToolResult(
                    success=True,
                    summary=f"本地库中没有找到关于 '{query}' 的内容，建议使用 web_search 外搜",
                    data={"results": [], "query": query},
                )

            formatted = []
            results_data = []
            for item in items:
                date_str = (
                    item.report.report_date.isoformat() if item.report else "未知"
                )
                formatted.append(
                    f"- [{item.title}]({item.source_url})\n"
                    f"  来源: {item.source_name} | 日期: {date_str}\n"
                    f"  摘要: {item.summary[:150]}"
                )
                results_data.append(
                    {
                        "title": item.title,
                        "url": item.source_url,
                        "source_name": item.source_name,
                        "summary": item.summary,
                        "date": date_str,
                        "section": item.section,
                    }
                )

            summary = f"本地库找到 {len(items)} 条相关内容：\n\n" + "\n\n".join(
                formatted
            )
            return ToolResult(
                success=True,
                summary=summary,
                data={"results": results_data, "query": query, "count": len(items)},
            )
        except Exception as exc:
            logger.warning("[LocalCorpusSearch] Failed: %s", exc)
            return ToolResult(success=False, summary=f"本地搜索失败: {exc}", data={})


# ── Research Finish Tool ──────────────────────────────────


class ResearchFinishTool(Tool):
    """完成研究，输出带引用的回答。"""

    name = "finish"
    description = (
        "完成研究，输出最终回答。"
        "此工具将停止研究并返回给用户。"
        "确保答案有完整引用后再调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "对用户问题的完整回答（markdown 格式）",
            },
            "summary": {
                "type": "string",
                "description": "一句话摘要",
            },
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
                "description": "引用列表",
            },
        },
        "required": ["answer"],
    }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        answer: str = kwargs.get("answer", "")
        citations: list[dict] = kwargs.get("citations", [])
        summary: str = kwargs.get("summary", "")

        if not answer:
            return ToolResult(success=False, summary="answer 不能为空", data={})

        return ToolResult(
            success=True,
            summary=f"✅ 研究完成",
            data={
                "answer": answer,
                "summary": summary,
                "citations": citations,
                "is_finish": True,
            },
        )


# ── Result Dataclass ──────────────────────────────────────


@dataclass
class ResearchResult:
    """研究 Agent 的输出结果。"""

    answer: str
    citations: list[dict[str, str]] = field(default_factory=list)
    mode: str = "agent"
    step_count: int = 0

    def to_chat_response(self) -> tuple[str, list[dict], str]:
        """转换为 ChatService 期望的格式。"""
        return self.answer, self.citations, self.mode


# ── Main Agent Class ──────────────────────────────────────


class ResearchAgent:
    """
    研究型 Agent。用户提问驱动的多步研究。

    对外接口：
      agent = ResearchAgent(session)
      result = await agent.research(question="...")
      answer, citations, mode = result.to_chat_response()
    """

    def __init__(self, session: Any = None) -> None:
        self._session = session
        self._llm_client = LLMClient()

    def _build_harness(self) -> Harness:
        from app.services.harness import (
            DEFAULT_BLOCKED_DOMAINS,
            DEFAULT_DOMAIN_KEYWORDS,
        )

        return Harness(
            max_steps=25,
            max_search_calls=10,
            max_page_reads=8,
            max_duration_seconds=180.0,
            max_llm_calls=15,
            domain_keywords=list(DEFAULT_DOMAIN_KEYWORDS),
            blocked_domains=list(DEFAULT_BLOCKED_DOMAINS),
            system_prompt=RESEARCH_SYSTEM_PROMPT,
        )

    def _build_tools(self) -> list[Tool]:
        from app.services.brave import BraveSearchClient
        from app.services.scraper import ScraperClient

        brave = BraveSearchClient()
        scraper = ScraperClient()
        return [
            LocalCorpusSearchTool(session=self._session),
            WebSearchTool(brave_client=brave),
            ReadPageTool(scraper_client=scraper),
            FollowReferencesTool(),
            EvaluateArticleTool(llm_client=self._llm_client),
            CompareSourcesTool(llm_client=self._llm_client),
            ResearchFinishTool(),
        ]

    async def research(self, question: str) -> ResearchResult:
        """
        对用户问题进行多步研究。

        Args:
            question: 用户的自然语言问题

        Returns:
            ResearchResult: 带引用的研究结果
        """
        tools = self._build_tools()
        harness = self._build_harness()
        agent = AgentCore(tools=tools, llm_client=self._llm_client, harness=harness)

        task = f"用户问题：{question}\n\n请先搜索本地日报库，再决定是否需要外搜。给出完整的带引用回答后，调用 finish 输出。"

        try:
            agent_result = await agent.run(task=task)
            return self._extract_research_result(agent_result)
        except Exception as exc:
            logger.error("[ResearchAgent] Failed: %s", exc, exc_info=True)
            return ResearchResult(
                answer=f"研究过程遇到问题：{exc}。请稍后重试或换一种提问方式。",
                citations=[],
                mode="error",
            )

    @staticmethod
    def _extract_research_result(agent_result: AgentResult) -> ResearchResult:
        """从 AgentResult 中提取研究结果。"""
        # 从 step history 中找 finish 工具的输出
        memory_snapshot = agent_result.memory_snapshot
        step_count = agent_result.step_count

        # finish 工具的数据通过 agent_result.title/summary 带出来
        answer = agent_result.summary or agent_result.title or "未能生成完整回答。"

        # 构建引用列表（从已收集的文章）
        citations = []
        for article in agent_result.articles[:5]:
            if article.get("url") and article.get("title"):
                citations.append(
                    {
                        "label": article.get("source_name")
                        or article.get("domain", ""),
                        "url": article.get("url", ""),
                        "title": article.get("title", ""),
                    }
                )

        return ResearchResult(
            answer=answer,
            citations=citations,
            mode="agent"
            if agent_result.finished_reason == "finish_tool"
            else "agent_partial",
            step_count=step_count,
        )
