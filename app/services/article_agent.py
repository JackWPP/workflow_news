"""
article_agent.py — 文章处理 Sub-Agent

每个 ArticleAgent 处理一篇文章 URL 的完整流水线：
  1. read_page → 获取全文
  2. evaluate_article → 评估价值
  3. search_images → 找配图（可选）
  4. verify_image → 验证配图（可选）

不是 LLM Agent Loop，而是确定性的异步流水线。
因为步骤是固定的，LLM 循环会浪费 token 和延迟。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.working_memory import ImageCandidate

if TYPE_CHECKING:
    from app.services.tools import Tool
    from app.services.working_memory import WorkingMemory

logger = logging.getLogger(__name__)


@dataclass
class ArticleCard:
    """Article Agent 的结构化输出。"""
    url: str
    title: str
    domain: str
    source_name: str
    published_at: str | None
    summary: str
    section: str              # academic / industry / policy / rejected
    key_finding: str
    image_url: str | None = None
    image_caption: str | None = None
    evaluation_reason: str = ""
    success: bool = True
    error: str | None = None
    steps_used: int = 0
    tokens_used: int = 0


@dataclass
class ArticleHarness:
    """单篇文章处理子 Agent 的轻量预算。"""
    max_steps: int = 8
    max_llm_calls: int = 3    # evaluate + verify + retry
    max_duration_seconds: float = 60.0


class ArticleAgent:
    """
    轻量级 Sub-Agent：处理单篇文章。

    固定流水线（非 LLM 循环），共享 WorkingMemory 和 Tool 实例。
    """

    def __init__(
        self,
        url: str,
        context: str,
        memory: "WorkingMemory",
        tools: dict[str, "Tool"],
        harness: ArticleHarness | None = None,
        agent_run_id: int | None = None,
        session: Any | None = None,
    ) -> None:
        self.url = url
        self.context = context
        self.memory = memory
        self.tools = tools
        self.harness = harness or ArticleHarness()
        self.agent_run_id = agent_run_id
        self.session = session

    async def run(self) -> ArticleCard:
        """执行文章处理流水线。"""
        start = time.time()
        steps = 0

        # Step 1: read_page
        read_result = await self.tools["read_page"].execute(
            memory=self.memory, url=self.url,
        )
        steps += 1
        if not read_result.success:
            return ArticleCard(
                url=self.url, title="", domain="", source_name="",
                published_at=None, summary="", section="rejected",
                key_finding="", success=False,
                error=f"read_page 失败: {read_result.summary}",
                steps_used=steps,
            )

        page_data = read_result.data
        title = page_data.get("title", "")
        domain = page_data.get("domain", "")
        content_summary = page_data.get("content_summary", "")
        inline_image = page_data.get("image_url")
        published_at = page_data.get("published_at")

        # Step 2: evaluate_article（含一次重试）
        eval_result = await self._evaluate_with_retry(title, content_summary, domain, published_at)
        steps += 1

        if not eval_result.data.get("worthy", False):
            return ArticleCard(
                url=self.url, title=title, domain=domain,
                source_name=domain, published_at=published_at,
                summary=content_summary[:200], section="rejected",
                key_finding="", success=True,
                evaluation_reason=eval_result.data.get("reason", "评估未通过"),
                steps_used=steps,
            )

        section = eval_result.data.get("section", "industry")
        if section not in {"academic", "industry", "policy"}:
            section = "industry"
        key_finding = eval_result.data.get("key_finding", title[:50])
        zh_title = eval_result.data.get("zh_title") or title
        zh_summary = eval_result.data.get("zh_summary") or content_summary[:200]
        image_worthiness = eval_result.data.get("image_worthiness", True)

        # Step 3: search_images（可选）
        image_url: str | None = None
        image_caption: str | None = None

        if image_worthiness:
            # 优先使用页面内联图
            if inline_image:
                image_url = inline_image
                image_caption = title
                self.memory.add_image_candidate(
                    self.url,
                    ImageCandidate(
                        image_url=image_url,
                        source_url=self.url,
                        caption=image_caption,
                        relevance_score=0.8,
                        origin_type="article_inline",
                    ),
                )
            else:
                # 搜索配图
                img_result = await self.tools["search_images"].execute(
                    memory=self.memory,
                    topic=key_finding or title,
                    article_url=self.url,
                )
                steps += 1

                if img_result.success and img_result.data.get("candidates_added", 0) > 0:
                    best = self.memory.best_image_for_article(self.url)
                    if best:
                        image_url = best.image_url
                        image_caption = best.caption

            # Step 4: verify_image（可选）
            if image_url and "verify_image" in self.tools:
                verify_result = await self.tools["verify_image"].execute(
                    memory=self.memory,
                    image_url=image_url,
                    article_url=self.url,
                    context=key_finding or title,
                )
                steps += 1
                if not verify_result.data.get("suitable", True):
                    image_url = None
                    image_caption = None

        elapsed = time.time() - start
        logger.info(
            "[ArticleAgent] %s → section=%s image=%s (%.1fs, %d steps)",
            self.url[:60], section, "yes" if image_url else "no", elapsed, steps,
        )

        return ArticleCard(
            url=self.url,
            title=zh_title,
            domain=domain,
            source_name=domain,
            published_at=published_at,
            summary=zh_summary,
            section=section,
            key_finding=key_finding,
            image_url=image_url,
            image_caption=image_caption,
            evaluation_reason=eval_result.data.get("reason", ""),
            success=True,
            steps_used=steps,
        )

    async def _evaluate_with_retry(
        self, title: str, content: str, domain: str, published_at: str | None,
    ):
        """evaluate_article 带一次重试，防御 LLM 限流等瞬态失败。"""
        eval_result = await self.tools["evaluate_article"].execute(
            memory=self.memory,
            title=title,
            content=content,
            url=self.url,
            domain=domain,
            published_at=published_at or "",
        )
        if eval_result.success:
            return eval_result

        # 瞬态失败（如 429 限流），等待后重试一次
        logger.warning("[ArticleAgent] evaluate failed for %s, retrying in 2s: %s", self.url[:60], eval_result.summary)
        await asyncio.sleep(2.0)
        return await self.tools["evaluate_article"].execute(
            memory=self.memory,
            title=title,
            content=content,
            url=self.url,
            domain=domain,
            published_at=published_at or "",
        )
