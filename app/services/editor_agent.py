"""
editor_agent.py — 编辑型 Agent（目标 1 的核心）

职责：从 ArticlePool 选取种子 → 评估 → 对比 → 写板块 → 输出日报
没有 web_search 工具，不受任何外部 API 影响。
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import session_scope
from app.models import AgentRun, ArticlePool, Report, RetrievalRun
from app.services.agent_core import AgentCore, AgentResult
from app.services.editor_tools import ReadPoolArticleTool
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.source_quality import SOURCE_TIER_RANK, classify_source
from app.services.tools import (
    CheckCoverageTool,
    CompareSourcesTool,
    EvaluateArticleTool,
    FinishTool,
    ReadPageTool,
    SearchImagesTool,
    VerifyImageTool,
    WriteSectionTool,
)
from app.services.working_memory import WorkingMemory
from app.utils import now_local

logger = logging.getLogger(__name__)

_SEED_DOMAIN_CAP = 2
_SEED_LIMIT = 20
_LOW_VALUE_SEED_KINDS = {"marketing", "ecommerce", "aggregator", "content_platform"}
_SECTION_ORDER = ("industry", "academic", "policy")
_CATEGORY_ORDER = ("塑料", "橡胶", "纤维")
_SECTION_MIN_QUOTA: dict[str, int] = {"industry": 2, "academic": 2, "policy": 2}
_SEED_POSITIVE_KEYWORDS = (
    "高分子",
    "塑料",
    "树脂",
    "橡胶",
    "复合材料",
    "注塑",
    "挤出",
    "吹塑",
    "薄膜",
    "改性",
    "聚合物",
    "聚乳酸",
    "pbat",
    "pla",
    "polymer",
    "plastics",
    "plastic",
    "resin",
    "rubber",
    "composite",
    "injection",
    "extrusion",
    "biodegradable",
    "recycling",
    "membrane",
    "separator",
    "materials informatics",
)
_SEED_WATCH_ONLY_PATTERNS = (
    "价格表",
    "收盘价格",
    "行情报价",
    "现货价格",
    "期货",
    "供应商",
    "厂家",
    "视频-",
    "实验室概况",
    "行政许可",
    "服务事项",
    "教师主页",
    "主页管理系统",
    "论文--",
    "整改完成情况",
    "止痒",
    "为华课堂",
)

EDITOR_SYSTEM_PROMPT = """\
你是高分子材料加工日报的编辑。
你的任务是从已有的文章素材中筛选、评估、对比、撰写，产出一份高质量的行业日报。

【关键规则】
1. 你没有 web_search 工具——所有素材已经由记者预先采集到文章池中
2. 种子清单中的文章正文已预抓取，用 read_pool_article 直接读取
3. 如果某篇文章正文缺失（fetch_status != ok），用 read_page 尝试重新抓取
4. 评估每篇文章的价值（evaluate_article），过滤掉低价值内容
5. 对比多篇文章（compare_sources），发现趋势和关联
6. 撰写各板块（write_section），最后 finish 输出完整日报

【报告质量要求】
- 每个板块至少 2 篇文章支撑
- 事实陈述 + 行业影响分析 + 趋势预判
- 每条分析末尾附来源引用（超链接格式）
- 如果某板块只有 1 篇，深度展开其行业影响
"""


class EditorAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=40,
            max_duration_seconds=600.0,
            system_prompt=EDITOR_SYSTEM_PROMPT,
            min_sources_for_publish=1,
        )

    def _build_tools(self, scraper_client: Any = None) -> list:
        from app.services.scraper import ScraperClient
        scraper = scraper_client or ScraperClient()
        return [
            ReadPoolArticleTool(),
            ReadPageTool(scraper_client=scraper),
            EvaluateArticleTool(llm_client=self._llm_client),
            CompareSourcesTool(llm_client=self._llm_client),
            SearchImagesTool(scraper_client=scraper),
            VerifyImageTool(llm_client=self._llm_client),
            WriteSectionTool(llm_client=self._llm_client),
            CheckCoverageTool(),
            FinishTool(llm_client=self._llm_client),
        ]

    async def run(
        self,
        run_id: int | None = None,
        shadow_mode: bool | None = None,
        report_date: date | None = None,
        mode: str = "publish",
        event_queue: Any | None = None,
    ) -> Report:
        target_date = report_date or now_local().date()
        shadow = shadow_mode if shadow_mode is not None else settings.shadow_mode

        # 1. 选取种子（降级阶梯）
        seeds, seed_window = await self._select_seeds(target_date)
        if not seeds:
            logger.warning("[EditorAgent] No seeds available, generating empty report")
            return await self._empty_report(target_date, shadow)

        logger.info("[EditorAgent] Selected %d seeds (window=%s)", len(seeds), seed_window)

        # 2. 创建 DB 记录
        agent_run_id = None
        with session_scope() as session:
            if run_id is None:
                run = RetrievalRun(run_date=now_local(), shadow_mode=shadow)
                session.add(run)
                session.flush()
                run_id = run.id
            agent_run = AgentRun(retrieval_run_id=run_id, agent_type="editor_agent")
            session.add(agent_run)
            session.flush()
            agent_run_id = agent_run.id
            session.commit()

        # 3. 构建 task prompt（种子显式枚举）
        task = self._build_task_prompt(target_date, seeds, seed_window)

        # 4. 运行 Agent
        tools = self._build_tools()
        harness = self._build_harness()

        # 把 seeds 注入 working memory，让它们在 LLM 调 read_pool_article 之前
        # 就已对系统可见——这一方面让 _build_fallback_result 能在 budget 耗尽时
        # 至少看到候选信息（避免 publishable_articles==[] 直接 status=failed），
        # 另一方面也方便观测/调试时直接从 memory 看到种子列表。
        memory = WorkingMemory()
        for s in seeds:
            metadata = dict(s.get("metadata") or {})
            if s.get("section"):
                metadata.setdefault("intended_section", s.get("section"))
            if s.get("category"):
                metadata.setdefault("intended_category", s.get("category"))
            if s.get("source_tier") or s.get("source_kind"):
                metadata.setdefault(
                    "source_quality",
                    {
                        "source_tier": s.get("source_tier"),
                        "source_kind": s.get("source_kind"),
                    },
                )
            memory.search_results.append({
                "url": s.get("url", ""),
                "title": s.get("title", ""),
                "snippet": s.get("snippet", ""),
                "domain": s.get("domain", ""),
                "published_at": s.get("published_at"),
                "search_type": "article_pool",
                "source_name": s.get("domain", ""),
                "source_type": "article_pool",
                "section": s.get("section"),
                "category": s.get("category"),
                "metadata": {
                    **metadata,
                    "pool_id": s.get("id"),
                    "fetch_status": s.get("fetch_status"),
                    "section_hint": s.get("section") or "",
                },
            })
        if event_queue:
            try:
                event_queue.put_nowait({
                    "type": "stats",
                    "phase": "seed",
                    "seed_count": len(seeds),
                    "seed_window": seed_window,
                })
            except Exception:
                pass

        agent = AgentCore(tools=tools, llm_client=self._llm_client, harness=harness, event_queue=event_queue)
        result = await agent.run(task=task, agent_run_id=agent_run_id, memory=memory)

        # 5. 写入 Report
        return await self._result_to_report(
            result, target_date, run_id, agent_run_id, shadow, seeds
        )

    async def _select_seeds(
        self, target_date: date
    ) -> tuple[list[dict[str, Any]], str]:
        """降级阶梯选取种子。返回 (seeds, window_label)。"""
        windows = [
            (timedelta(hours=24), "fresh_24h", 6),
            (timedelta(hours=48), "extended_48h", 6),
            (timedelta(hours=72), "archive_72h", 6),
            (timedelta(days=7), "fallback_7d", 10),
        ]

        for delta, label, min_count in windows:
            since = target_date - delta
            with session_scope() as session:
                articles = list(session.scalars(
                    select(ArticlePool)
                    .where(
                        ArticlePool.ingested_at >= since,
                    )
                    .order_by(
                        ArticlePool.published_at.desc().nullslast(),
                        ArticlePool.ingested_at.desc(),
                    )
                    .limit(120)
                ).all())

            # 过滤掉无正文且无摘要的（相当于 permanent_fail）
            articles = [
                a for a in articles
                if (a.raw_content and len(a.raw_content.strip()) > 50)
                or (a.summary and len(a.summary.strip()) > 20)
            ]
            articles = self._rank_and_balance_seed_articles(
                articles, limit=_SEED_LIMIT
            )

            if len(articles) >= min_count:
                seeds = [self._article_to_seed(a) for a in articles]
                return seeds, label

        # 最后尝试：任何有 quality_score 的文章
        with session_scope() as session:
            articles = list(session.scalars(
                select(ArticlePool)
                .where(ArticlePool.quality_score.isnot(None))
                .order_by(ArticlePool.quality_score.desc())
                .limit(10)
            ).all())

        if articles:
            articles = self._rank_and_balance_seed_articles(
                articles, limit=min(10, _SEED_LIMIT), allow_low_value=False
            )
            return [self._article_to_seed(a) for a in articles], "best_available"

        return [], "empty"

    @staticmethod
    def _metadata(article: ArticlePool) -> dict[str, Any]:
        return dict(article.eval_metadata or {})

    @classmethod
    def _discovery_metadata(cls, article: ArticlePool) -> dict[str, Any]:
        metadata = cls._metadata(article)
        discovery = metadata.get("discovery")
        return dict(discovery or {})

    @classmethod
    def _source_quality(cls, article: ArticlePool) -> dict[str, Any]:
        metadata = cls._metadata(article)
        stored_quality = metadata.get("source_quality")
        if isinstance(stored_quality, dict) and stored_quality.get("source_tier"):
            return stored_quality
        return classify_source(
            url=article.url,
            title=article.title,
            content=(article.summary or article.raw_content or "")[:1500],
        )

    @classmethod
    def _seed_section(cls, article: ArticlePool, quality: dict[str, Any]) -> str:
        discovery = cls._discovery_metadata(article)
        section = article.section or discovery.get("intended_section") or ""
        if section in _SECTION_ORDER:
            return str(section)
        source_kind = str(quality.get("source_kind") or "")
        if source_kind in {"government", "standards"}:
            return "policy"
        if source_kind in {"academic_journal", "academic"}:
            return "academic"
        return "industry"

    @classmethod
    def _seed_category(cls, article: ArticlePool) -> str:
        discovery = cls._discovery_metadata(article)
        category = article.category or discovery.get("intended_category") or ""
        return str(category) if category in _CATEGORY_ORDER else "塑料"

    @classmethod
    def _seed_text(cls, article: ArticlePool) -> str:
        discovery = cls._discovery_metadata(article)
        return " ".join(
            [
                article.title or "",
                article.summary or "",
                str(discovery.get("search_query") or ""),
                str(discovery.get("query_family") or ""),
                str(discovery.get("intended_category") or ""),
            ]
        ).lower()

    @classmethod
    def _keyword_matches(cls, text: str, keyword: str) -> bool:
        needle = keyword.lower()
        if any("\u4e00" <= char <= "\u9fff" for char in needle):
            return needle in text
        if " " in needle:
            return needle in text
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None

    @classmethod
    def _seed_is_relevant(cls, article: ArticlePool) -> bool:
        text = cls._seed_text(article)
        if any(pattern.lower() in text for pattern in _SEED_WATCH_ONLY_PATTERNS):
            return False
        return any(cls._keyword_matches(text, keyword) for keyword in _SEED_POSITIVE_KEYWORDS)

    @classmethod
    def _seed_score(
        cls,
        article: ArticlePool,
        *,
        quality: dict[str, Any],
        section: str,
        category: str,
    ) -> float:
        score = 0.0
        score += float(SOURCE_TIER_RANK.get(str(quality.get("source_tier") or "C"), 2)) * 2.0
        if article.raw_content and len(article.raw_content.strip()) > 300:
            score += 2.0
        elif article.raw_content and len(article.raw_content.strip()) > 50:
            score += 1.0
        if article.published_at:
            score += 1.5
        if article.source_type == "rss":
            score += 2.0
        if section in {"academic", "policy"}:
            score += 0.6
        source_kind = str(quality.get("source_kind") or "")
        if source_kind in {"government", "academic_journal", "official_company_newsroom"}:
            score += 1.2
        return score

    @classmethod
    def _rank_and_balance_seed_articles(
        cls,
        articles: list[ArticlePool],
        *,
        limit: int = _SEED_LIMIT,
        allow_low_value: bool = False,
    ) -> list[ArticlePool]:
        scored: list[tuple[float, ArticlePool, str, str]] = []
        for article in articles:
            if not cls._seed_is_relevant(article):
                continue
            quality = cls._source_quality(article)
            source_tier = str(quality.get("source_tier") or "C")
            source_kind = str(quality.get("source_kind") or "")
            if not allow_low_value and (
                source_tier == "D"
                or source_kind in _LOW_VALUE_SEED_KINDS
                or quality.get("publish_block_reason")
            ):
                continue
            section = cls._seed_section(article, quality)
            category = cls._seed_category(article)
            scored.append(
                (
                    cls._seed_score(
                        article, quality=quality, section=section, category=category
                    ),
                    article,
                    section,
                    category,
                )
            )

        scored.sort(
            key=lambda item: (
                item[0],
                item[1].published_at or item[1].ingested_at,
                item[1].ingested_at,
            ),
            reverse=True,
        )

        buckets: dict[tuple[str, str], list[tuple[float, ArticlePool, str, str]]] = {}
        for item in scored:
            buckets.setdefault((item[2], item[3]), []).append(item)

        selected: list[ArticlePool] = []
        selected_urls: set[str] = set()
        domain_counts: dict[str, int] = {}

        def try_add(item: tuple[float, ArticlePool, str, str]) -> bool:
            article = item[1]
            if article.url in selected_urls:
                return False
            if domain_counts.get(article.domain, 0) >= _SEED_DOMAIN_CAP:
                return False
            selected.append(article)
            selected_urls.add(article.url)
            domain_counts[article.domain] = domain_counts.get(article.domain, 0) + 1
            return True

        while len(selected) < limit:
            progressed = False
            for section in _SECTION_ORDER:
                for category in _CATEGORY_ORDER:
                    bucket = buckets.get((section, category), [])
                    while bucket:
                        item = bucket.pop(0)
                        if try_add(item):
                            progressed = True
                            break
                    if len(selected) >= limit:
                        break
                if len(selected) >= limit:
                    break
            if not progressed:
                break

        section_counts: dict[str, int] = {}
        for article in selected:
            sec = article.section or ""
            section_counts[sec] = section_counts.get(sec, 0) + 1

        for sec, min_count in _SECTION_MIN_QUOTA.items():
            while section_counts.get(sec, 0) < min_count:
                added = False
                for item in scored:
                    if len(selected) >= limit:
                        break
                    item_section = item[2]
                    if item_section != sec:
                        continue
                    if try_add(item):
                        section_counts[sec] = section_counts.get(sec, 0) + 1
                        added = True
                        break
                if not added:
                    break

        if len(selected) < limit:
            for item in scored:
                if len(selected) >= limit:
                    break
                try_add(item)

        return selected

    @staticmethod
    def _article_to_seed(article: ArticlePool) -> dict[str, Any]:
        has_content = bool(article.raw_content and len(article.raw_content.strip()) > 50)
        metadata = dict(article.eval_metadata or {})
        discovery = dict(metadata.get("discovery") or {})
        quality = EditorAgent._source_quality(article)
        section = article.section or discovery.get("intended_section") or ""
        category = article.category or discovery.get("intended_category") or ""
        return {
            "id": article.id,
            "url": article.url,
            "title": article.title,
            "domain": article.domain,
            "snippet": (article.summary or "")[:200],
            "fetch_status": "ok" if has_content else "empty",
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "section": section,
            "category": category,
            "source_tier": quality.get("source_tier", ""),
            "source_kind": quality.get("source_kind", ""),
            "metadata": metadata,
        }

    def _build_task_prompt(
        self, target_date: date, seeds: list[dict], seed_window: str
    ) -> str:
        now = now_local()

        # 种子编号列表
        seed_lines = []
        for i, s in enumerate(seeds, 1):
            status = "正文✓" if s["fetch_status"] == "ok" else f"仅摘要({s['fetch_status']})"
            section_hint = f"[{s['section']}]" if s["section"] else ""
            category_hint = f"[{s.get('category')}]" if s.get("category") else ""
            source_hint = f"{s.get('source_tier') or '?'}:{s.get('source_kind') or '?'}"
            seed_lines.append(
                f"- [{i}] (id={s['id']}) {s['title'][:60]}（{s['domain']}）"
                f"{section_hint}{category_hint} {source_hint} {status}"
            )
        seed_block = "\n".join(seed_lines)

        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请生成今日《{settings.report_title}》（{target_date.isoformat()}）。\n\n"
            f"种子清单（共 {len(seeds)} 条，窗口={seed_window}）：\n{seed_block}\n\n"
            f"工作流程：\n"
            f"1. 用 read_pool_article 读取种子正文（用编号对应的 id）\n"
            f"2. 每读完一组，立即用 evaluate_article 评估\n"
            f"3. 正文缺失的种子，用 read_page 尝试重新抓取\n"
            f"4. 评估完成后，用 compare_sources 做对比分析\n"
            f"5. 用 write_section 撰写各板块\n"
            f"6. 用 finish 输出完整日报\n\n"
            f"【效率提示——务必遵守】\n"
            f"- 一轮内最多 read_pool_article 4 篇 + evaluate_article 4 篇，避免一次塞太多\n"
            f"- 评估到 4-6 篇 worth_publishing 的文章后，**停止读新种子**，直接 write_section\n"
            f"- 写完所有有内容的板块后**立即调用 finish**，不要重复评估或对比\n"
            f"- 整个流程预期 8-14 轮 LLM 决策内完成；超过 14 轮请直接 finish\n\n"
            f"你没有 web_search 工具。所有素材已在种子清单中。\n"
            f"时效要求：优先使用过去 36 小时内发布的文章。\n"
        )

    async def _result_to_report(
        self,
        result: AgentResult,
        target_date: date,
        run_id: int,
        agent_run_id: int,
        shadow: bool,
        seeds: list[dict],
    ) -> Report:
        from app.services.daily_report_agent import DailyReportAgent
        from app.services.repository import get_report_settings
        dra = DailyReportAgent()
        with session_scope() as session:
            runtime = dra._runtime_settings(get_report_settings(session), shadow)
        return await dra._result_to_report(
            result, target_date, run_id, agent_run_id,
            shadow, "publish",
            runtime,
            self._llm_client, self._llm_client,
        )

    async def _empty_report(self, target_date: date, shadow: bool) -> Report:
        """无种子时生成休刊占位报告。"""
        with session_scope() as session:
            report = Report(
                report_date=target_date,
                status="empty",
                title=f"{settings.report_title}（{target_date.isoformat()}）— 休刊",
                markdown_content="今日无可用素材，休刊。",
                summary="无种子可用",
                pipeline_version="editor_agent",
            )
            session.add(report)
            session.commit()
            return report
