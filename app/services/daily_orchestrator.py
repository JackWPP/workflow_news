from __future__ import annotations

import asyncio
import logging
from datetime import date
from time import perf_counter
from typing import Any

from app.models import AgentRun, Report, RetrievalRun
from app.services.agent_core import AgentResult
from app.services.explorer_agent import ExplorerAgent
from app.services.llm_client import LLMClient
from app.services.section_editor_agent import SectionEditorAgent
from app.services.summary_agent import SummaryAgent
from app.utils import now_local

logger = logging.getLogger(__name__)

CATEGORIES: list[str] = ["塑料", "橡胶", "纤维"]


class DailyOrchestrator:
    """日报编排器 — 协调多个 Agent 并行执行"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def run(
        self,
        run_id: int | None = None,
        event_queue: asyncio.Queue | None = None,
        shadow_mode: bool | None = None,
    ) -> Report:
        """
        执行完整的日报生成流程。

        流程:
        1. 并行运行 3 个 ExplorerAgent（按材料方向）
        2. 并行运行 3 个 SectionEditorAgent（按材料方向）
        3. 运行 SummaryAgent
        4. 保存到数据库并返回 Report
        """
        from app.database import session_scope
        from app.services.repository import get_report_settings

        started = perf_counter()
        target_date = now_local().date()

        # 创建 RetrievalRun 和 AgentRun 记录
        agent_run_id = None
        with session_scope() as session:
            if run_id is None:
                run = RetrievalRun(run_date=now_local(), shadow_mode=shadow_mode or False)
                session.add(run)
                session.flush()
                run_id = run.id
            agent_run = AgentRun(retrieval_run_id=run_id, agent_type="daily_orchestrator")
            session.add(agent_run)
            session.flush()
            agent_run_id = agent_run.id
            session.commit()

        # 执行三阶段流程
        explore_results = await self._phase_explore(run_id, event_queue)
        edit_results = await self._phase_edit(explore_results, run_id, event_queue)
        summary_result = await self._phase_summary(edit_results, run_id, event_queue)

        elapsed = round(perf_counter() - started, 2)
        all_cards = [card for cards in edit_results for card in cards]

        logger.info(
            "[DailyOrchestrator] Finished in %.1fs: %d cards",
            elapsed,
            len(all_cards),
        )

        # 转换为 AgentResult 并保存到数据库
        report = await self._result_to_report(
            cards=all_cards,
            summary_result=summary_result,
            target_date=target_date,
            run_id=run_id,
            agent_run_id=agent_run_id,
            shadow_mode=shadow_mode or False,
            elapsed=elapsed,
        )

        return report

    async def _phase_explore(
        self,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[list[dict[str, Any]]]:
        if event_queue:
            await event_queue.put({"type": "phase", "phase": "explore"})

        explorers = [
            ExplorerAgent(category, self._llm)
            for category in CATEGORIES
        ]

        tasks = []
        for category, explorer in zip(CATEGORIES, explorers):
            tasks.append(
                self._run_explorer(explorer, category, run_id, event_queue)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        explore_output: list[list[dict[str, Any]]] = []
        for category, result in zip(CATEGORIES, results):
            if isinstance(result, Exception):
                logger.error(
                    "[DailyOrchestrator] Explorer %s failed: %s",
                    category,
                    result,
                )
                explore_output.append([])
            elif isinstance(result, list) and len(result) == 0:
                logger.warning(
                    "[DailyOrchestrator] Explorer %s returned 0 candidates — "
                    "possible causes: all rejected, no search results, or budget exhausted",
                    category,
                )
                explore_output.append(result)
            else:
                logger.info(
                    "[DailyOrchestrator] Explorer %s returned %d candidates",
                    category,
                    len(result),
                )
                explore_output.append(result)

        total_candidates = sum(len(r) for r in explore_output)
        if total_candidates == 0:
            logger.error(
                "[DailyOrchestrator] ALL Explorers returned 0 candidates! Report will be empty."
            )
        elif total_candidates < 6:
            per_cat = {
                cat: len(r) for cat, r in zip(CATEGORIES, explore_output)
            }
            logger.warning(
                "[DailyOrchestrator] Low candidate count: %d total — breakdown: %s",
                total_candidates,
                per_cat,
            )

        return explore_output

    async def _run_explorer(
        self,
        explorer: ExplorerAgent,
        category: str,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[dict[str, Any]]:
        if event_queue:
            await event_queue.put(
                {"type": "progress", "phase": "explore", "category": category}
            )
        try:
            candidates = await explorer.explore(run_id)
            logger.info(
                "[DailyOrchestrator] Explorer %s found %d candidates",
                category,
                len(candidates),
            )
            return candidates
        except Exception as exc:
            logger.error(
                "[DailyOrchestrator] Explorer %s error: %s",
                category,
                exc,
                exc_info=True,
            )
            raise

    async def _phase_edit(
        self,
        explore_results: list[list[dict[str, Any]]],
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[list[dict[str, Any]]]:
        if event_queue:
            await event_queue.put({"type": "phase", "phase": "edit"})

        editors = [
            SectionEditorAgent(category, self._llm)
            for category in CATEGORIES
        ]

        tasks = []
        for category, editor, candidates in zip(CATEGORIES, editors, explore_results):
            tasks.append(
                self._run_editor(editor, candidates, category, run_id, event_queue)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        edit_output: list[list[dict[str, Any]]] = []
        for category, result in zip(CATEGORIES, results):
            if isinstance(result, Exception):
                logger.error(
                    "[DailyOrchestrator] Editor %s failed: %s",
                    category,
                    result,
                )
                edit_output.append([])
            else:
                edit_output.append(result)

        return edit_output

    async def _run_editor(
        self,
        editor: SectionEditorAgent,
        candidates: list[dict[str, Any]],
        category: str,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[dict[str, Any]]:
        if event_queue:
            await event_queue.put(
                {"type": "progress", "phase": "edit", "category": category}
            )
        try:
            cards = await editor.edit(candidates, run_id)
            logger.info(
                "[DailyOrchestrator] Editor %s produced %d cards",
                category,
                len(cards),
            )
            return cards
        except Exception as exc:
            logger.error(
                "[DailyOrchestrator] Editor %s error: %s",
                category,
                exc,
                exc_info=True,
            )
            raise

    async def _phase_summary(
        self,
        edit_results: list[list[dict[str, Any]]],
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> dict[str, Any]:
        if event_queue:
            await event_queue.put({"type": "phase", "phase": "summary"})

        all_cards = [card for cards in edit_results for card in cards]

        if not all_cards:
            logger.warning("[DailyOrchestrator] No cards to summarize")
            return SummaryAgent._empty_result()

        summary_agent = SummaryAgent(self._llm)
        return await summary_agent.generate(all_cards, run_id)

    async def _result_to_report(
        self,
        cards: list[dict[str, Any]],
        summary_result: dict[str, Any],
        target_date: date,
        run_id: int,
        agent_run_id: int,
        shadow_mode: bool,
        elapsed: float,
    ) -> Report:
        """将 cards 转换为 AgentResult 并保存到数据库。"""
        from app.database import session_scope
        from app.services.report_persistence import result_to_report
        from app.services.repository import get_report_settings

        # 构建 sections_content：按 category 分组
        sections_content: dict[str, str] = {}
        category_cards: dict[str, list[dict[str, Any]]] = {}
        for card in cards:
            category = card.get("category", "塑料")
            category_cards.setdefault(category, []).append(card)

        # 为每个 category 生成 section markdown
        for category, cat_cards in category_cards.items():
            section_lines = [f"## {category}\n"]
            for card in cat_cards:
                title = card.get("title", "")
                source = card.get("domain", "")
                summary = card.get("summary", "") or card.get("snippet", "")
                url = card.get("url", "")
                section_lines.append(f"### {title}")
                section_lines.append(f"**来源**: {source}")
                if summary:
                    section_lines.append(f"\n{summary}")
                if url:
                    section_lines.append(f"\n[阅读原文]({url})")
                section_lines.append("")
            sections_content[category] = "\n".join(section_lines)

        # 构建 articles 列表（用于 result_to_report）
        articles = []
        for i, card in enumerate(cards):
            articles.append({
                "title": card.get("title", ""),
                "url": card.get("url", ""),
                "domain": card.get("domain", ""),
                "summary": card.get("summary", "") or card.get("snippet", ""),
                "section": card.get("section", "industry"),
                "category": card.get("category", "塑料"),
                "source_name": card.get("source_name", ""),
                "source_tier": card.get("source_tier", ""),
                "source_kind": card.get("source_kind", ""),
                "key_finding": card.get("key_finding", ""),
                "evaluation_reason": card.get("editor_notes", "") or card.get("evaluation_reason", ""),
                "image_url": card.get("image_url", ""),
                "published_at": card.get("published_at"),
                "relevance_score": card.get("combined_score", 0.6),
                "language": card.get("language", "zh"),
            })

        # 构建 AgentResult
        # summary_result 包含: html, summary, foresight, trends, follow_up
        result = AgentResult(
            success=len(cards) > 0,
            title=f"高分子材料加工每日资讯 ({target_date.isoformat()})",
            summary=summary_result.get("summary", "无摘要"),
            articles=articles,
            sections_content=sections_content,
            editorial=summary_result.get("foresight", ""),
            daily_briefing="\n".join(summary_result.get("trends", [])),
            memory_snapshot={
                "categories": {cat: len(cards) for cat, cards in category_cards.items()},
                "total_cards": len(cards),
                "elapsed_seconds": elapsed,
                "trends": summary_result.get("trends", []),
                "follow_up": summary_result.get("follow_up", []),
            },
            harness_status={},
            finished_reason="complete" if cards else "no_articles",
            step_count=0,
            total_tokens=0,
            diagnostics={},
        )

        # 获取 runtime 设置
        with session_scope() as session:
            runtime = self._runtime_settings(get_report_settings(session), shadow_mode)

        # 调用 result_to_report 保存到数据库
        report = await result_to_report(
            result=result,
            target_date=target_date,
            run_id=run_id,
            agent_run_id=agent_run_id,
            shadow_mode=shadow_mode,
            mode="publish",
            runtime=runtime,
            llm_client=self._llm,
            synthesis_llm_client=self._llm,
        )

        logger.info(
            "[DailyOrchestrator] Report saved: id=%d, status=%s, articles=%d",
            report.id,
            report.status,
            len(articles),
        )

        return report

    @staticmethod
    def _runtime_settings(payload: dict[str, Any] | None, shadow_mode: bool | None) -> dict[str, Any]:
        """构建 runtime 设置，与 DailyReportAgent 保持一致。"""
        from app.config import settings
        payload = payload or {}
        return {
            "shadow_mode": settings.shadow_mode if shadow_mode is None else shadow_mode,
            "scrape_timeout_seconds": int(
                payload.get("scrape_timeout_seconds", settings.scrape_timeout_seconds)
            ),
            "scrape_concurrency": max(
                1, int(payload.get("scrape_concurrency", settings.scrape_concurrency))
            ),
            "max_extractions_per_run": max(
                1,
                int(
                    payload.get(
                        "max_extractions_per_run", settings.max_extractions_per_run
                    )
                ),
            ),
            "domain_failure_threshold": max(
                1,
                int(
                    payload.get(
                        "domain_failure_threshold", settings.domain_failure_threshold
                    )
                ),
            ),
            "report_primary_model": payload.get(
                "report_primary_model", settings.report_primary_model
            ),
            "report_fallback_model": payload.get(
                "report_fallback_model", settings.report_fallback_model
            ),
            "strict_primary_model_for_tool_use": bool(
                payload.get(
                    "strict_primary_model_for_tool_use",
                    settings.strict_primary_model_for_tool_use,
                )
            ),
            "strict_primary_model_for_all_llm": bool(
                payload.get(
                    "strict_primary_model_for_all_llm",
                    settings.strict_primary_model_for_all_llm,
                )
            ),
            "tool_use_fallback_mode": payload.get(
                "tool_use_fallback_mode", settings.tool_use_fallback_mode
            ),
            "report_min_formal_topics": max(
                1,
                int(
                    payload.get(
                        "report_min_formal_topics", settings.report_min_formal_topics
                    )
                ),
            ),
            "report_target_items": max(
                1, int(payload.get("report_target_items", settings.report_target_items))
            ),
        }
