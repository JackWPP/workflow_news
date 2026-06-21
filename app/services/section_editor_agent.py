"""
section_editor_agent.py — 板块编辑 Agent

消费 Explorer 的候选文章，执行去重、排序、评审，生成带编辑备注的卡片。
使用 AgentCore 但只暴露评估/对比/写板块/检查覆盖四个工具。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from app.services.agent_core import AgentCore, AgentResult
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.source_quality import SOURCE_TIER_RANK
from app.services.tools import (
    CheckCoverageTool,
    CompareSourcesTool,
    EvaluateArticleTool,
    FinishTool,
    WriteSectionTool,
)
from app.services.working_memory import ArticleSummary, WorkingMemory
from app.utils import now_local

logger = logging.getLogger(__name__)

_TITLE_SIMILARITY_THRESHOLD = 0.75

_KEYWORDS = {
    "塑料": [
        "塑料","树脂","注塑","挤出","吹塑","薄膜","改性","聚乙烯","聚丙烯","聚苯乙烯",
        "聚氯乙烯","PET","PVC","PE","PP","PS","PLA","PBAT","PBS","EVA","POE",
        "回收","再生塑料","限塑","禁塑","碳关税","CBAM","隔膜","胶膜","封装",
        "质子交换膜","导电高分子","聚苯胺","3D打印","增材制造",
        "plastic","polymer","resin","injection","extrusion","film","recycling","biodegradable",
    ],
    "橡胶": [
        "橡胶","轮胎","合成橡胶","天然橡胶","丁苯橡胶","丁腈橡胶","硅橡胶","氟橡胶",
        "聚氨酯","弹性体","热塑性弹性体","TPU","TPE","TPV","硫化","密炼","开炼",
        "密封件","胶管","胶带","输送带","减震",
        "rubber","tire","tyre","elastomer","vulcanization","SBR","NBR","EPDM",
    ],
    "纤维": [
        "纤维","碳纤维","芳纶","超高分子量聚乙烯纤维","涤纶","锦纶","尼龙","丙纶",
        "腈纶","氨纶","维纶","粘胶","化纤","纺丝","熔体纺丝","湿法纺丝","静电纺丝",
        "玻璃纤维","预浸料",
        "fiber","fibre","carbon fiber","aramid","UHMWPE","spinning","nylon","polyester","prepreg",
    ],
}

SECTION_EDITOR_SYSTEM_PROMPT = """\
你是高分子材料加工日报的板块编辑。
你的任务是对已筛选的候选文章进行最终评审，生成带编辑备注的卡片。

【工作流程】
1. 用 evaluate_article 逐篇评估候选文章的价值
2. 用 compare_sources 对比多篇文章，发现重复和互补
3. 用 check_coverage 确认板块覆盖是否达标
4. 用 write_section 撰写板块内容
5. 用 finish 输出最终卡片列表

【关键规则】
- 只评估候选清单中的文章，不主动搜索
- 每篇文章必须有 editor_notes（编辑备注）
- 去重：同一事件只保留最佳来源
- 按 source_tier + 时效性排序，优先保留高质量来源
"""


def _title_similarity(a: str, b: str) -> float:
    a_norm = re.sub(r"\s+", "", a.lower())
    b_norm = re.sub(r"\s+", "", b.lower())
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _keyword_score(text: str, category: str) -> float:
    keywords = _KEYWORDS.get(category, [])
    if not keywords:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return hits / len(keywords)


def _parse_published_at(candidate: dict[str, Any]) -> datetime | None:
    raw = candidate.get("published_at")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    unique: list[dict[str, Any]] = []

    for c in candidates:
        url = (c.get("url") or "").strip()
        if not url:
            continue
        if url in seen_urls:
            continue

        title = c.get("title") or ""
        is_dup = False
        for existing in unique:
            existing_title = existing.get("title") or ""
            if _title_similarity(title, existing_title) >= _TITLE_SIMILARITY_THRESHOLD:
                existing_tier = SOURCE_TIER_RANK.get(existing.get("source_tier", "C"), 2)
                current_tier = SOURCE_TIER_RANK.get(c.get("source_tier", "C"), 2)
                if current_tier > existing_tier:
                    unique.remove(existing)
                    seen_urls.discard(existing.get("url", ""))
                else:
                    is_dup = True
                break
        if not is_dup:
            seen_urls.add(url)
            unique.append(c)

    return unique


def rank_candidates(
    candidates: list[dict[str, Any]],
    category: str = "",
) -> list[dict[str, Any]]:
    now = now_local()

    def score_key(c: dict[str, Any]) -> tuple[float, float, float]:
        tier = SOURCE_TIER_RANK.get(c.get("source_tier", "C"), 2)
        pub = _parse_published_at(c)
        if pub:
            age_hours = max((now.replace(tzinfo=None) - pub.replace(tzinfo=None)).total_seconds() / 3600, 0)
            freshness = max(0.0, 1.0 - age_hours / 168.0)
        else:
            freshness = 0.3
        text = f"{c.get('title', '')} {c.get('summary', '')} {c.get('key_finding', '')}"
        kw = _keyword_score(text, category)
        return (tier * 2.0 + freshness * 1.5 + kw * 3.0, tier, freshness)

    return sorted(candidates, key=score_key, reverse=True)


class SectionEditorAgent:
    """板块编辑 Agent — 评审、入库、生成卡片"""

    def __init__(
        self,
        category: str,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.category = category
        self._llm = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=20,
            max_duration_seconds=240.0,
            system_prompt=SECTION_EDITOR_SYSTEM_PROMPT,
            min_sources_for_publish=1,
        )

    def _build_tools(self) -> list:
        return [
            EvaluateArticleTool(llm_client=self._llm),
            CompareSourcesTool(llm_client=self._llm),
            WriteSectionTool(llm_client=self._llm),
            CheckCoverageTool(),
            FinishTool(llm_client=self._llm),
        ]

    def _build_task_prompt(self, candidates: list[dict[str, Any]]) -> str:
        now = now_local()
        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            tier = c.get("source_tier", "?")
            pub = c.get("published_at") or "未知时间"
            key = c.get("key_finding", "")
            candidate_lines.append(
                f"- [{i}] {c.get('title', 'Untitled')[:60]}（{c.get('domain', '')}）"
                f" 等级:{tier} 日期:{pub}"
                + (f" 核心:{key}" if key else "")
            )
        candidate_block = "\n".join(candidate_lines)

        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}\n\n"
            f"方向：{self.category}\n\n"
            f"候选清单（共 {len(candidates)} 条，已去重排序）：\n{candidate_block}\n\n"
            f"工作流程：\n"
            f"1. 用 evaluate_article 评估每篇候选文章\n"
            f"2. 用 compare_sources 对比去重\n"
            f"3. 用 check_coverage 确认覆盖\n"
            f"4. 用 write_section 撰写板块\n"
            f"5. 用 finish 输出最终卡片列表\n\n"
            f"每篇文章必须有 editor_notes（编辑备注，50字以内）。\n"
        )

    def _candidates_to_memory(
        self, memory: WorkingMemory, candidates: list[dict[str, Any]]
    ) -> None:
        for c in candidates:
            url = c.get("url", "")
            title = c.get("title", "")
            domain = c.get("domain", "")
            if not url or not title:
                continue
            memory.search_results.append({
                "url": url,
                "title": title,
                "snippet": c.get("summary", "")[:200],
                "domain": domain,
                "published_at": c.get("published_at"),
                "search_type": "explorer_candidate",
                "source_name": c.get("source_name", domain),
                "source_type": "explorer_candidate",
                "source_tier": c.get("source_tier", "C"),
                "source_kind": c.get("source_kind", "general_site"),
                "metadata": {
                    "explorer_candidate": True,
                    "why_selected": c.get("why_selected", ""),
                    "category": self.category,
                },
            })

    async def edit(
        self,
        candidates: list[dict[str, Any]],
        run_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if not candidates:
            logger.warning(
                "[SectionEditorAgent] %s: No candidates provided — "
                "section will be empty. Root cause: Explorer returned 0 articles.",
                self.category,
            )
            return []

        deduped = deduplicate_candidates(candidates)
        ranked = rank_candidates(deduped, category=self.category)

        logger.info(
            "[SectionEditorAgent] %s: %d candidates → %d deduped → %d ranked",
            self.category,
            len(candidates),
            len(deduped),
            len(ranked),
        )

        tools = self._build_tools()
        harness = self._build_harness()
        agent = AgentCore(tools=tools, llm_client=self._llm, harness=harness)

        memory = WorkingMemory()
        self._candidates_to_memory(memory, ranked)

        task = self._build_task_prompt(ranked)
        result: AgentResult = await agent.run(task=task, memory=memory, agent_run_id=run_id)

        cards = self._build_cards(ranked, result, memory)

        logger.info(
            "[SectionEditorAgent] %s finished: %d cards, reason=%s",
            self.category,
            len(cards),
            result.finished_reason,
        )
        return cards

    def _build_cards(
        self,
        ranked: list[dict[str, Any]],
        result: AgentResult,
        memory: WorkingMemory,
    ) -> list[dict[str, Any]]:
        pub_articles = memory.publishable_articles()
        pub_urls = {a.url for a in pub_articles}
        pub_map = {a.url: a for a in pub_articles}

        cards: list[dict[str, Any]] = []
        for i, c in enumerate(ranked):
            url = c.get("url", "")
            card_section = c.get("section", "industry")
            if url in pub_map:
                card_section = pub_map[url].section or card_section
            card: dict[str, Any] = {
                **c,
                "section": card_section,
                "category": self.category,
                "rank": i + 1,
                "status": "approved" if url in pub_urls else "draft",
                "editor_notes": c.get("why_selected", ""),
            }

            if url in pub_map:
                article = pub_map[url]
                if article.evaluation_reason:
                    card["editor_notes"] = article.evaluation_reason
                if article.key_finding and not card.get("key_finding"):
                    card["key_finding"] = article.key_finding

            cards.append(card)

        approved_count = sum(1 for card in cards if card["status"] == "approved")
        if approved_count == 0 and cards:
            for card in cards[:3]:
                card["status"] = "approved"
                if not card["editor_notes"]:
                    card["editor_notes"] = "自动批准（无 LLM 评估结果）"

        return cards
