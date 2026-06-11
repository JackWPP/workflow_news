"""EditorAgent 专用工具集。"""
from __future__ import annotations

import logging
from typing import Any

from app.services.tools import Tool, ToolResult
from app.services.working_memory import WorkingMemory

logger = logging.getLogger(__name__)


class ReadPoolArticleTool(Tool):
    """从 ArticlePool 读取已预抓取的正文。零网络、零超时。"""

    name = "read_pool_article"
    description = (
        "从文章池中读取一篇已预抓取的文章正文。"
        "这是你的主要文章来源——种子清单中的文章正文已经提前抓取好了。"
        "读完后请立即用 evaluate_article 评估文章价值。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "article_id": {
                "type": "integer",
                "description": "文章 ID（种子清单中编号对应的 ID）",
            },
            "url": {
                "type": "string",
                "description": "文章 URL（如果不知道 article_id，可以用 URL 查找）",
            },
        },
        "required": [],
    }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        from app.database import session_scope
        from app.models import ArticlePool
        from sqlalchemy import select

        article_id = kwargs.get("article_id")
        url = kwargs.get("url", "").strip()

        if not article_id and not url:
            return ToolResult(success=False, summary="需要提供 article_id 或 url", data={})

        try:
            with session_scope() as session:
                if article_id:
                    article = session.get(ArticlePool, int(article_id))
                else:
                    article = session.scalar(
                        select(ArticlePool).where(ArticlePool.url == url)
                    )

                if article is None:
                    return ToolResult(
                        success=False,
                        summary=f"文章未找到（id={article_id}, url={url[:60]}）",
                        data={},
                    )

                # 构造返回数据
                has_content = bool(article.raw_content and len(article.raw_content.strip()) > 50)
                data = {
                    "id": article.id,
                    "url": article.url,
                    "title": article.title,
                    "domain": article.domain,
                    "published_at": article.published_at.isoformat() if article.published_at else None,
                    "summary": article.summary or "",
                    "content": article.raw_content if has_content else "",
                    "has_content": has_content,
                    "fetch_status": "ok" if has_content else "empty",
                    "section": article.section or "",
                    "quality_score": article.quality_score,
                }

                if has_content:
                    # 截断到合理长度给 LLM（保留前 8000 字符）
                    content_preview = article.raw_content[:8000]
                    if len(article.raw_content) > 8000:
                        content_preview += f"\n\n[... 正文共 {len(article.raw_content)} 字符，已截断 ...]"
                    summary = (
                        f"📄 {article.title}\n"
                        f"来源: {article.domain} | 日期: {article.published_at or '未知'}\n"
                        f"正文（{len(article.raw_content)} 字符）:\n{content_preview}"
                    )
                else:
                    summary = (
                        f"📄 {article.title}\n"
                        f"来源: {article.domain}\n"
                        f"⚠️ 正文未抓取成功，"
                        f"仅有摘要: {(article.summary or '')[:200]}\n"
                        f"建议：用 read_page 工具尝试重新抓取此 URL: {article.url}"
                    )

                return ToolResult(success=True, summary=summary, data=data)

        except Exception as exc:
            logger.warning("ReadPoolArticleTool failed: %s", exc)
            return ToolResult(success=False, summary=f"读取失败: {exc}", data={})
