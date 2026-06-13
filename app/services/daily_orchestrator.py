from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from app.services.explorer_agent import ExplorerAgent
from app.services.llm_client import LLMClient
from app.services.section_editor_agent import SectionEditorAgent
from app.services.summary_agent import SummaryAgent

logger = logging.getLogger(__name__)

SECTIONS: list[tuple[str, str]] = [
    ("industry", "高材制造"),
    ("policy", "清洁能源"),
    ("academic", "AI"),
]


class DailyOrchestrator:
    """日报编排器 — 协调多个 Agent 并行执行"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def run(
        self,
        run_id: int | None = None,
        event_queue: asyncio.Queue | None = None,
    ) -> dict[str, Any]:
        """
        执行完整的日报生成流程。

        流程:
        1. 并行运行 3 个 ExplorerAgent
        2. 并行运行 3 个 SectionEditorAgent
        3. 运行 SummaryAgent
        4. 返回结果
        """
        started = perf_counter()

        explore_results = await self._phase_explore(run_id, event_queue)
        edit_results = await self._phase_edit(explore_results, run_id, event_queue)
        result = await self._phase_summary(edit_results, run_id, event_queue)

        elapsed = round(perf_counter() - started, 2)
        all_cards = [card for cards in edit_results for card in cards]
        result["cards"] = all_cards
        result["meta"] = {
            "total_cards": len(all_cards),
            "elapsed_seconds": elapsed,
            "sections": {s: len(c) for s, c in zip(SECTIONS, edit_results)},
        }

        logger.info(
            "[DailyOrchestrator] Finished in %.1fs: %d cards",
            elapsed,
            len(all_cards),
        )
        return result

    async def _phase_explore(
        self,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[list[dict[str, Any]]]:
        if event_queue:
            await event_queue.put({"type": "phase", "phase": "explore"})

        explorers = [
            ExplorerAgent(section, category, self._llm)
            for section, category in SECTIONS
        ]

        tasks = []
        for (section, category), explorer in zip(SECTIONS, explorers):
            tasks.append(
                self._run_explorer(explorer, section, category, run_id, event_queue)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        explore_output: list[list[dict[str, Any]]] = []
        for (section, category), result in zip(SECTIONS, results):
            if isinstance(result, Exception):
                logger.error(
                    "[DailyOrchestrator] Explorer %s/%s failed: %s",
                    section,
                    category,
                    result,
                )
                explore_output.append([])
            else:
                explore_output.append(result)

        return explore_output

    async def _run_explorer(
        self,
        explorer: ExplorerAgent,
        section: str,
        category: str,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[dict[str, Any]]:
        if event_queue:
            await event_queue.put(
                {"type": "progress", "phase": "explore", "section": section, "category": category}
            )
        try:
            candidates = await explorer.explore(run_id)
            logger.info(
                "[DailyOrchestrator] Explorer %s/%s found %d candidates",
                section,
                category,
                len(candidates),
            )
            return candidates
        except Exception as exc:
            logger.error(
                "[DailyOrchestrator] Explorer %s/%s error: %s",
                section,
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
            SectionEditorAgent(section, category, self._llm)
            for section, category in SECTIONS
        ]

        tasks = []
        for (section, category), editor, candidates in zip(SECTIONS, editors, explore_results):
            tasks.append(
                self._run_editor(editor, candidates, section, category, run_id, event_queue)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        edit_output: list[list[dict[str, Any]]] = []
        for (section, category), result in zip(SECTIONS, results):
            if isinstance(result, Exception):
                logger.error(
                    "[DailyOrchestrator] Editor %s/%s failed: %s",
                    section,
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
        section: str,
        category: str,
        run_id: int | None,
        event_queue: asyncio.Queue | None,
    ) -> list[dict[str, Any]]:
        if event_queue:
            await event_queue.put(
                {"type": "progress", "phase": "edit", "section": section, "category": category}
            )
        try:
            cards = await editor.edit(candidates, run_id)
            logger.info(
                "[DailyOrchestrator] Editor %s/%s produced %d cards",
                section,
                category,
                len(cards),
            )
            return cards
        except Exception as exc:
            logger.error(
                "[DailyOrchestrator] Editor %s/%s error: %s",
                section,
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
