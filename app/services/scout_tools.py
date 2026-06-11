"""ScoutAgent 专用工具集。"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.database import session_scope
from app.models import ArticlePool
from app.services.tools import Tool, ToolResult
from app.services.working_memory import WorkingMemory
from app.utils import now_local

logger = logging.getLogger(__name__)


class CheckPoolGapsTool(Tool):
    """检查文章池中各板块的覆盖情况，发现缺口。"""

    name = "check_pool_gaps"
    description = (
        "查看文章池中各板块（industry/policy/academic）过去 24 小时的文章数量。"
        "用于发现哪些方向需要补充搜索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "查看过去多少小时的数据（默认 24）",
            },
        },
        "required": [],
    }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        hours = int(kwargs.get("hours", 24))
        cutoff = now_local() - timedelta(hours=hours)

        try:
            with session_scope() as session:
                # 按 section 统计
                rows = session.execute(
                    select(
                        ArticlePool.section,
                        func.count(ArticlePool.id),
                    )
                    .where(ArticlePool.ingested_at >= cutoff)
                    .group_by(ArticlePool.section)
                ).all()

                # 按 language 统计
                lang_rows = session.execute(
                    select(
                        ArticlePool.language,
                        func.count(ArticlePool.id),
                    )
                    .where(ArticlePool.ingested_at >= cutoff)
                    .group_by(ArticlePool.language)
                ).all()

                # 最近入池的文章
                recent = list(session.scalars(
                    select(ArticlePool)
                    .where(ArticlePool.ingested_at >= cutoff)
                    .order_by(ArticlePool.ingested_at.desc())
                    .limit(5)
                ).all())

            section_counts = {row[0] or "unclassified": row[1] for row in rows}
            lang_counts = {row[0]: row[1] for row in lang_rows}

            # 识别缺口
            expected_sections = {"industry", "policy", "academic"}
            covered = {s for s in section_counts if s in expected_sections}
            gaps = expected_sections - covered

            summary_parts = [
                f"📊 过去 {hours} 小时文章池状态：",
                f"板块分布: {section_counts}",
                f"语言分布: {lang_counts}",
                f"总入池: {sum(section_counts.values())} 条",
            ]
            if gaps:
                summary_parts.append(f"⚠️ 缺口板块: {', '.join(gaps)}")
                summary_parts.append("建议: 优先搜索缺口方向的内容")
            else:
                summary_parts.append("✅ 三大板块均有覆盖")

            if recent:
                summary_parts.append("\n最近入池:")
                for a in recent[:3]:
                    summary_parts.append(f"  - {a.title[:50]}（{a.domain}）")

            return ToolResult(
                success=True,
                summary="\n".join(summary_parts),
                data={
                    "section_counts": section_counts,
                    "lang_counts": lang_counts,
                    "gaps": list(gaps),
                    "total": sum(section_counts.values()),
                },
            )
        except Exception as exc:
            logger.warning("CheckPoolGapsTool failed: %s", exc)
            return ToolResult(success=False, summary=f"查询失败: {exc}", data={})


class BochaAiSearchTool(Tool):
    """使用 Bocha AI Search 进行发散探索，返回 AI 摘要和追问建议。"""

    name = "ai_search"
    description = (
        "AI 驱动的深度搜索。返回搜索结果的同时，"
        "还提供 AI 生成的摘要和追问建议（followup_questions），"
        "你可以顺着追问继续挖深。适合发散探索和发现新方向。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索词",
            },
            "language": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "搜索语言",
            },
        },
        "required": ["query"],
    }

    def __init__(self, bocha_client: Any = None) -> None:
        self._bocha = bocha_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        from app.services.tools import _YEAR_TOKEN_RE
        import re

        query: str = kwargs.get("query", "").strip()
        language: str = kwargs.get("language", "zh")

        if not query:
            return ToolResult(success=False, summary="query 不能为空", data={})

        # 去年份
        normalized = _YEAR_TOKEN_RE.sub("", query)
        normalized = re.sub(r"\s{2,}", " ", normalized).strip() or query.strip()

        if memory.has_searched(normalized):
            return ToolResult(
                success=False,
                summary=f"'{normalized}' 已经搜索过，请换一个不同的搜索词",
                data={"already_searched": True},
            )

        memory.record_search(normalized)

        if not self._bocha or not self._bocha.enabled:
            return ToolResult(success=False, summary="Bocha AI Search 不可用", data={})

        try:
            results = await self._bocha.ai_search(normalized, count=10)
        except Exception as exc:
            logger.warning("BochaAiSearchTool failed for '%s': %s", normalized[:50], exc)
            return ToolResult(success=False, summary=f"AI 搜索失败: {exc}", data={})

        if not results:
            return ToolResult(
                success=True,
                summary=f"AI 搜索 '{normalized}' 无结果",
                data={"results": [], "query": normalized},
            )

        # 提取 AI 摘要和追问建议
        ai_answer = results[0].get("ai_answer", "") if results else ""
        followup = results[0].get("followup_questions", []) if results else []

        # 记录搜索结果到 memory
        memory.record_search_results(normalized, results)

        # 格式化
        formatted = []
        for r in results[:8]:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = (r.get("snippet") or "")[:150]
            formatted.append(f"- [{title}]({url})\n  {snippet}")

        summary_parts = [f"🔍 AI 搜索 '{normalized}' → {len(results)} 条结果"]
        if ai_answer:
            summary_parts.append(f"\n💡 AI 摘要:\n{ai_answer[:500]}")
        if followup:
            summary_parts.append(f"\n🤔 追问建议:")
            for q in followup[:3]:
                summary_parts.append(f"  - {q}")
        summary_parts.append("\n搜索结果:")
        summary_parts.extend(formatted)

        return ToolResult(
            success=True,
            summary="\n".join(summary_parts),
            data={
                "results": [dict(r) for r in results],
                "query": normalized,
                "ai_answer": ai_answer,
                "followup_questions": followup,
            },
        )
