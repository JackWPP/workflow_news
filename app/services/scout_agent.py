"""
scout_agent.py — 搜索型 Agent（目标 2 的载体）

职责：发现文章 → 阅读 → 评估 → 入池
发散能力：Bocha AI Search 的 followup_questions 顺藤摸瓜
失败只损失增量，不影响 EditorAgent 的日报产出。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import session_scope
from app.models import ArticlePool
from app.services.agent_core import AgentCore, AgentResult
from app.services.bocha_search import BochaSearchClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.scraper import ScraperClient
from app.services.scout_tools import BochaAiSearchTool, CheckPoolGapsTool
from app.services.tools import (
    EvaluateArticleTool,
    FinishTool,
    ReadPageTool,
    WebSearchTool,
)
from app.services.working_memory import WorkingMemory
from app.utils import canonicalize_url, now_local

logger = logging.getLogger(__name__)

SCOUT_SYSTEM_PROMPT = """\
你是高分子材料加工领域的情报搜索员（"记者"）。
你的任务是发现有价值的文章并将其存入文章池，供后续编辑使用。

【工作流程】
1. 先用 check_pool_gaps 查看各板块覆盖情况
2. 缺哪个方向就搜哪个方向
3. 用 web_search 或 ai_search 搜索新内容
4. ai_search 返回的追问建议（followup_questions）可以顺藤摸瓜
5. 发现有价值的链接后，用 read_page 深入阅读
6. 阅读后用 evaluate_article 评估
7. 评估达标的文章会自动入池（系统处理）
8. 调用 finish 输出本轮发现总结

【搜索策略】
- 中英文各半，覆盖设备/原料/政策/学术/应用等方向
- 不要反复搜同一主题的近义词
- ai_search 适合发散探索，web_search 适合精准搜索
- 学术方向可以搜 "polymer composite research 2026" 等
- 政策方向可以搜 "限塑令 碳关税 CBAM" 等

【关键约束】
- 总共搜索 5-8 轮就够了
- 每轮搜索后立即阅读和评估，不要囤积
- 评估有价值的文章会自动入池——你不需要手动写入数据库
- budget 有限，高效利用每一步
"""


class ScoutAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=25,
            max_duration_seconds=300.0,
            system_prompt=SCOUT_SYSTEM_PROMPT,
        )

    def _build_tools(self) -> list:
        bocha = BochaSearchClient()
        scraper = ScraperClient()
        return [
            CheckPoolGapsTool(),
            WebSearchTool(bocha_client=bocha),
            BochaAiSearchTool(bocha_client=bocha),
            ReadPageTool(scraper_client=scraper),
            EvaluateArticleTool(llm_client=self._llm_client),
            FinishTool(llm_client=self._llm_client),
        ]

    async def run(self) -> dict[str, Any]:
        """运行一轮搜索。返回统计信息。"""
        tools = self._build_tools()
        harness = self._build_harness()
        agent = AgentCore(tools=tools, llm_client=self._llm_client, harness=harness)

        task = (
            f"当前时间：{now_local().isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请执行一轮情报搜索，为《{settings.report_title}》补充文章素材。\n"
            f"先用 check_pool_gaps 查看缺口，再针对性搜索。\n"
            f"评估有价值的文章会自动入池。完成后调用 finish 总结本轮发现。\n"
        )

        result = await agent.run(task=task)

        # 将 evaluate 达标的文章写入 ArticlePool
        added = await self._promote_articles(result)

        return {
            "success": result.success,
            "step_count": result.step_count,
            "articles_found": len(result.articles),
            "articles_added": added,
            "finished_reason": result.finished_reason,
        }

    async def _promote_articles(self, result: AgentResult) -> int:
        """将 Agent 发现的有价值文章写入 ArticlePool。"""
        if not result.articles:
            return 0

        added = 0
        for article_data in result.articles:
            if not article_data.get("worth_publishing"):
                continue

            url = article_data.get("url", "")
            if not url:
                continue

            try:
                with session_scope() as session:
                    existing = session.scalar(
                        select(ArticlePool).where(ArticlePool.url == url)
                    )
                    if existing:
                        # 更新质量分数
                        if article_data.get("quality_score"):
                            existing.quality_score = article_data["quality_score"]
                        if article_data.get("section"):
                            existing.section = article_data["section"]
                        continue

                    normalized = canonicalize_url(url)
                    content_hash = hashlib.sha256(
                        f"{article_data.get('title', '')}|{normalized}".encode()
                    ).hexdigest()

                    new_article = ArticlePool(
                        url=normalized,
                        content_hash=content_hash,
                        title=article_data.get("title", "")[:1024],
                        domain=article_data.get("domain", ""),
                        source_type="scout_agent",
                        language=article_data.get("language", "zh"),
                        summary=article_data.get("summary", "")[:2000],
                        raw_content=None,
                        published_at=None,
                        quality_score=article_data.get("quality_score"),
                        section=article_data.get("section"),
                        category=article_data.get("category"),
                    )
                    session.add(new_article)
                    session.commit()
                    added += 1
                    logger.info(
                        "ScoutAgent: promoted '%s' to pool",
                        article_data.get("title", "")[:50],
                    )
            except Exception as exc:
                logger.warning(
                    "ScoutAgent: failed to promote '%s': %s", url[:60], exc
                )

        return added
