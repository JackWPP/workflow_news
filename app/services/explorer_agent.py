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

CATEGORY_QUERIES: dict[str, list[dict[str, str]]] = {
    "塑料": [
        {"query": "注塑机 新品 2026", "language": "zh"},
        {"query": "塑料原料 价格行情", "language": "zh"},
        {"query": "改性塑料 应用", "language": "zh"},
        {"query": "塑料回收 限塑令 政策", "language": "zh"},
        {"query": "锂电池 隔膜 材料", "language": "zh"},
        {"query": "PLA PBAT 生物降解", "language": "zh"},
        {"query": "polymer plastic industry news", "language": "en"},
        {"query": "injection molding machine new product", "language": "en"},
    ],
    "橡胶": [
        {"query": "轮胎 行业动态 产能 2026", "language": "zh"},
        {"query": "合成橡胶 价格 行情", "language": "zh"},
        {"query": "弹性体 TPU TPE 新材料", "language": "zh"},
        {"query": "橡胶 密封件 汽车应用", "language": "zh"},
        {"query": "橡胶 行业标准 政策", "language": "zh"},
        {"query": "rubber tire industry news", "language": "en"},
        {"query": "synthetic rubber price capacity", "language": "en"},
        {"query": "elastomer research progress", "language": "zh"},
    ],
    "纤维": [
        {"query": "碳纤维 应用 产业化 2026", "language": "zh"},
        {"query": "化学纤维 涤纶 锦纶 行情", "language": "zh"},
        {"query": "芳纶 高性能纤维 应用", "language": "zh"},
        {"query": "纺丝 工艺 技术 突破", "language": "zh"},
        {"query": "碳纤维 复合材料 研究", "language": "zh"},
        {"query": "carbon fiber application industry", "language": "en"},
        {"query": "aramid fiber high performance", "language": "en"},
        {"query": "chemical fiber polyester nylon", "language": "en"},
    ],
}

EXPLORER_SYSTEM_PROMPT = """\
你是高分子材料加工领域的方向搜索专员。
你的任务是：针对指定方向（{category}），搜索、阅读、筛选高质量文章。

【工作流程】
1. 使用 web_search 执行推荐查询，每次搜索后检查结果
2. 从搜索结果中选出最相关的 2-4 个链接，用 read_page 批量阅读
3. 阅读后立刻用 evaluate_article 评估每篇文章的价值
4. 目标：找到 4-6 篇高质量文章。搜到足够文章后调用 finish
5. 不要在单一主题上过度搜索——覆盖多个子话题更重要

【搜索策略——目标导向】
- 执行推荐查询，每轮搜索最多 2 个 query（保持多样性）
- 每次 read_page 可以同时读 2-4 篇文章（并行效率更高）
- 如果某个 query 返回的结果全都不相关，换一个子话题方向搜索
- 搜索→阅读→评估 应形成紧凑循环：搜一轮 → 读最好的几篇 → 立刻评估
- 中英文结合，覆盖学术(研究突破/高校进展)、产业(市场行情/产能/新品/会议)、政策(法规/标准/规划)三个板块维度
- 不要反复搜索近义词（如搜了"注塑机 新品"就不要再搜"注塑成型 新设备"）
- 当找到 4+ 篇有价值的文章时，停止搜索，调用 finish

【来源接受标准——宽松】
- 在此专业B2B领域，大部分行业信息来自行业垂直媒体、行业协会、企业官网
- 以下来源均为合法信息源：行业媒体网站、B2B行业平台、协会官网、企业新闻中心、财经频道产业版块、政府网站、学术期刊
- 仅需跳过：纯电商产品页(alibaba/1688)、纯SEO营销页、百科词条
- 不要因为来源"不够权威"就跳过——在高分子材料B2B领域，行业垂直媒体就是主要信息载体

【关键约束】
- 优先收录过去 72 小时内发布的内容（政策/学术可放宽至 7 天）
- 每篇文章必须有明确的关键发现
- 每篇文章必须有入选原因

【方向特定指导】
- 塑料: 关注注塑/挤出/吹塑设备、树脂原料价格、改性塑料、薄膜、回收与限塑政策、生物降解材料、功能薄膜(隔膜/胶膜/质子膜)应用
- 橡胶: 关注轮胎产业、合成橡胶与天然橡胶行情、弹性体(TPU/TPE/TPV)、硫化工艺、密封件与汽车橡胶件、橡胶标准政策
- 纤维: 关注碳纤维产业化、化学纤维(涤纶/锦纶/丙纶)行情、芳纶等高性能纤维、纺丝工艺、纤维复合材料、静电纺丝研究
"""


def get_search_queries(category: str) -> list[dict[str, str]]:
    return CATEGORY_QUERIES.get(category, [])


def _build_explorer_prompt(category: str) -> str:
    return EXPLORER_SYSTEM_PROMPT.format(category=category)


class ExplorerAgent:
    """方向探索 Agent — 搜索、筛选、补充入选原因"""

    def __init__(
        self,
        category: str,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.category = category
        self._llm = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=32,
            max_duration_seconds=420.0,
            system_prompt=_build_explorer_prompt(self.category),
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
        queries = get_search_queries(self.category)
        query_lines = "\n".join(
            f"- {q['query']}（{q['language']}）" for q in queries
        )
        now = now_local()
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
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
            "[ExplorerAgent] %s finished: %d articles, reason=%s",
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
            "section": article.get("section", "industry"),
        }
