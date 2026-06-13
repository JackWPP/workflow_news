from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.services.agent_core import AgentCore, AgentResult
from app.services.bocha_search import BochaSearchClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.scraper import ScraperClient
from app.services.source_quality import classify_source
from app.services.tools import EvaluateArticleTool, FinishTool, ReadPageTool, WebSearchTool
from app.services.working_memory import WorkingMemory
from app.utils import extract_domain, now_local

logger = logging.getLogger(__name__)

SECTION_CATEGORY_QUERIES: dict[tuple[str, str], list[dict[str, str]]] = {
    ("industry", "高材制造"): [
        {"query": "注塑机 新品 2026", "language": "zh"},
        {"query": "挤出设备 技术升级", "language": "zh"},
        {"query": "高分子材料 产能扩建", "language": "zh"},
        {"query": "复合材料 汽车轻量化", "language": "zh"},
        {"query": "polymer processing equipment new", "language": "en"},
    ],
    ("policy", "清洁能源"): [
        {"query": "塑料回收 政策 2026", "language": "zh"},
        {"query": "碳关税 塑料行业", "language": "zh"},
        {"query": "电池隔膜 高分子", "language": "zh"},
        {"query": "plastic recycling regulation", "language": "en"},
    ],
    ("academic", "AI"): [
        {"query": "polymer machine learning property prediction", "language": "en"},
        {"query": "materials informatics polymer", "language": "en"},
        {"query": "injection molding AI optimization", "language": "en"},
    ],
}

EXPLORER_SYSTEM_PROMPT = """\
你是高分子材料加工领域的板块搜索专员。
你的任务是：针对指定板块（{section}）和方向（{category}），搜索、阅读、筛选高质量文章。

【工作流程】
1. 使用 web_search 按推荐查询搜索（可自行扩展相关查询）
2. 搜索结果中筛选有价值的链接，用 read_page 深入阅读
3. 阅读后用 evaluate_article 评估文章价值
4. 评估达标后，为每篇文章补充"入选原因"（why_selected）
5. 完成后调用 finish 输出候选文章列表

【搜索策略】
- 针对 {category} 方向，搜索 2-3 轮（不要超过 3 轮）
- 中英文结合，覆盖设备/原料/政策/学术/应用
- 不要反复搜同一主题的近义词
- 发现有价值的结果后立即阅读和评估
- 搜索预算有限，请高效使用

【关键约束】
- 只收录过去 72 小时内发布的内容
- 跳过明显的垃圾源（电商、营销、SEO 农场）
- 政府网站（.gov.cn）、大学网站（.edu.cn）、学术期刊（Nature/ACS/ScienceDirect）是高可信源，优先收录
- 企业 newsroom、行业协会、行业媒体也是优质来源
- 不要因为页面是"新闻列表"或"政策公告"就拒绝——只要有实质内容就应收录
- 每篇文章必须有明确的 key_finding（核心发现）
- 每篇文章必须有 why_selected（入选原因）

【板块特定指导】
- industry: 关注设备、原料、产能、应用等产业动态
- policy: 关注政策法规、标准、环保、回收等政策动向
- academic: 关注论文、研究、技术突破等学术进展
"""


def get_search_queries(section: str, category: str) -> list[dict[str, str]]:
    key = (section, category)
    return SECTION_CATEGORY_QUERIES.get(key, [])


def _build_explorer_prompt(section: str, category: str) -> str:
    return EXPLORER_SYSTEM_PROMPT.format(section=section, category=category)


class ExplorerAgent:
    """板块探索 Agent — 搜索、筛选、补充入选原因"""

    def __init__(
        self,
        section: str,
        category: str,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.section = section
        self.category = category
        self._llm = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=12,
            max_duration_seconds=240.0,
            system_prompt=_build_explorer_prompt(self.section, self.category),
        )

    def _build_tools(self) -> list:
        bocha = BochaSearchClient()
        scraper = ScraperClient()
        from app.services.search_router import SearchRouter
        router = SearchRouter(bocha_client=bocha)
        return [
            WebSearchTool(bocha_client=bocha, search_router=router),
            ReadPageTool(scraper_client=scraper),
            EvaluateArticleTool(llm_client=self._llm),
            FinishTool(llm_client=self._llm),
        ]

    def _build_task_prompt(self) -> str:
        queries = get_search_queries(self.section, self.category)
        query_lines = "\n".join(
            f"- {q['query']}（{q['language']}）" for q in queries
        )
        now = now_local()
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"板块：{self.section}\n"
            f"方向：{self.category}\n\n"
            f"推荐搜索查询：\n{query_lines}\n\n"
            f"请按推荐查询依次搜索，也可自行扩展。"
            f"搜索后阅读有价值的文章，评估并补充入选原因。"
            f"完成后调用 finish 输出候选文章列表。\n"
        )

    async def explore(self, run_id: int | None = None) -> list[dict[str, Any]]:
        tools = self._build_tools()
        harness = self._build_harness()
        agent = AgentCore(tools=tools, llm_client=self._llm, harness=harness)
        memory = WorkingMemory()

        task = self._build_task_prompt()
        result: AgentResult = await agent.run(task=task, memory=memory)

        logger.info(
            "[ExplorerAgent] %s/%s finished: %d articles, reason=%s",
            self.section,
            self.category,
            len(result.articles),
            result.finished_reason,
        )

        candidates: list[dict[str, Any]] = []
        for article in result.articles:
            candidate = self._article_to_candidate(article)
            if candidate:
                candidates.append(candidate)

        return candidates

    @staticmethod
    def _article_to_candidate(article: dict[str, Any]) -> dict[str, Any] | None:
        url = article.get("url", "")
        title = article.get("title", "")
        if not url or not title:
            return None

        domain = article.get("domain") or extract_domain(url)
        quality = classify_source(url=url, title=title, content=article.get("summary", ""))
        if quality["source_tier"] == "D":
            return None
        if quality["page_kind"] in {"homepage", "product", "search", "navigation"}:
            return None

        why_selected = article.get("selection_reason") or article.get("evaluation_reason") or ""
        if not why_selected:
            key_finding = article.get("key_finding", "")
            source_tier = quality["source_tier"]
            why_selected = f"来源等级 {source_tier}"
            if key_finding:
                why_selected += f"，核心发现：{key_finding}"

        return {
            "title": title,
            "url": url,
            "domain": domain,
            "source_name": article.get("source_name", domain),
            "summary": article.get("summary", ""),
            "key_finding": article.get("key_finding", ""),
            "source_tier": quality["source_tier"],
            "source_kind": quality["source_kind"],
            "why_selected": why_selected,
            "image_url": article.get("image_url"),
            "published_at": article.get("published_at"),
            "category": article.get("category", ""),
        }
