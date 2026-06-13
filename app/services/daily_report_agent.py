import asyncio
import logging
from datetime import date
from typing import Any

from app.config import settings
from app.models import AgentRun, ArticlePool, Report, RetrievalRun
from sqlalchemy import select
from app.services.agent_core import AgentCore, AgentResult
from app.services.bocha_search import BochaSearchClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.jina_reader import JinaReaderClient
from app.services.scraper import ScraperClient
from app.services.working_memory import WorkingMemory
from app.utils import now_local
from app.database import session_scope

from app.services.daily_prompts import (
    DAILY_REPORT_SYSTEM_PROMPT,
    FALLBACK_SYSTEM_PROMPT,
    SEARCH_PHASE_SYSTEM_PROMPT,
    SYNTHESIS_PHASE_SYSTEM_PROMPT,
)
from app.services.candidate_scorer import (
    _SEARCH_RECENCY_LABEL,
    _TRUSTED_SOURCE_SEED_LIMIT,
    _TRUSTED_SOURCE_ITEMS_PER_FEED,
    _TRUSTED_SOURCE_TIER_RANK,
    _POSITIVE_KEYWORDS,
    _NEGATIVE_KEYWORDS,
    _PREVIEW_REJECT_PAGE_KINDS,
    extract_candidates,
    candidate_score,
    candidate_section_hints,
)
from app.services.report_persistence import (
    auto_publish_status,
    publish_grade_from_status,
    result_to_report,
)
from app.services.repository import get_report_settings, list_sources
from app.services.rss import fetch_feed_entries

logger = logging.getLogger(__name__)


class DailyReportAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()
        self._runtime_llm: LLMClient | None = None

    def _runtime_settings(
        self, payload: dict[str, Any] | None, shadow_mode: bool | None
    ) -> dict[str, Any]:
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

    def _build_runtime_llm_client(self, runtime: dict[str, Any]) -> LLMClient:
        return LLMClient(
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            timeout=self._llm_client.timeout,
            strict_primary_model_for_tool_use=runtime[
                "strict_primary_model_for_tool_use"
            ],
            strict_primary_model_for_all_llm=runtime[
                "strict_primary_model_for_all_llm"
            ],
            tool_use_fallback_mode=runtime["tool_use_fallback_mode"],
        )

    def _build_synthesis_llm_client(self, runtime: dict[str, Any]) -> LLMClient:
        return LLMClient(
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            timeout=self._llm_client.timeout,
            strict_primary_model_for_tool_use=runtime[
                "strict_primary_model_for_tool_use"
            ],
            strict_primary_model_for_all_llm=runtime[
                "strict_primary_model_for_all_llm"
            ],
            tool_use_fallback_mode=runtime["tool_use_fallback_mode"],
        )

    def _build_agent_harness(self) -> Harness:
        return Harness(
            max_steps=65,
            max_duration_seconds=1200.0,
            system_prompt=FALLBACK_SYSTEM_PROMPT,
        )

    # ── Main Entry ───────────────────────────────────────────

    async def run(
        self,
        run_id: int | None = None,
        shadow_mode: bool | None = None,
        report_date: date | None = None,
        mode: str = "publish",
        event_queue: asyncio.Queue | None = None,
    ) -> Report:
        target_date = report_date or now_local().date()
        from app.database import session_scope as _session_scope

        with _session_scope() as session:
            runtime = self._runtime_settings(get_report_settings(session), shadow_mode)
        logger.info("[DailyReportAgent] Starting multi-agent run for: %s", target_date)

        if run_id is None:
            with _session_scope() as session:
                run_dt = now_local().replace(
                    year=target_date.year,
                    month=target_date.month,
                    day=target_date.day,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                run = RetrievalRun(
                    run_date=run_dt,
                    shadow_mode=runtime["shadow_mode"],
                )
                session.add(run)
                session.flush()

                agent_run = AgentRun(
                    retrieval_run_id=run.id,
                    agent_type="daily_report_v2",
                )
                session.add(agent_run)
                session.flush()
                session.commit()
                run_id = run.id
                agent_run_id = agent_run.id
        else:
            with _session_scope() as session:
                agent_run = AgentRun(
                    retrieval_run_id=run_id,
                    agent_type="daily_report_v2",
                )
                session.add(agent_run)
                session.flush()
                session.commit()
                agent_run_id = agent_run.id

        from app.log_context import run_id_var
        run_id_var.set(run_id)
        try:
            report = await self._run_phases(
                target_date,
                run_id,
                agent_run_id,
                shadow_mode,
                mode,
                event_queue,
                runtime,
            )
        except Exception as exc:
            logger.error("[DailyReportAgent] Pipeline failed: %s", exc, exc_info=True)
            try:
                with _session_scope() as session:
                    run_obj = session.get(RetrievalRun, run_id)
                    ar_obj = session.get(AgentRun, agent_run_id)
                    if run_obj:
                        run_obj.status = "failed"
                        run_obj.error_message = str(exc)[:500]
                        run_obj.finished_at = now_local()
                        crash_payload: dict[str, Any] = {
                            **(run_obj.debug_payload or {}),
                            "runtime": runtime,
                        }
                        if self._runtime_llm:
                            crash_payload["llm_metrics_on_crash"] = self._runtime_llm.snapshot_metrics()
                        run_obj.debug_payload = crash_payload
                    if ar_obj:
                        ar_obj.status = "failed"
                        ar_obj.finished_reason = "error"
                        ar_obj.total_steps = ar_obj.total_steps or 0
                        ar_obj.total_tokens = ar_obj.total_tokens or 0
            except Exception:
                logger.error(
                    "[DailyReportAgent] Also failed to update status", exc_info=True
                )
            raise

        return report

    async def _run_phases(
        self,
        target_date: date,
        run_id: int,
        agent_run_id: int,
        shadow_mode: bool | None,
        mode: str,
        event_queue: asyncio.Queue | None,
        runtime: dict[str, Any],
    ) -> Report:
        jina = JinaReaderClient()
        scraper = ScraperClient(jina_client=jina)
        bocha = BochaSearchClient()
        memory = WorkingMemory()
        llm_client = self._build_runtime_llm_client(runtime)
        synthesis_llm_client = self._build_synthesis_llm_client(runtime)
        self._runtime_llm = llm_client

        from app.services.composer import DailyComposer
        composer = DailyComposer(llm_client=llm_client)
        seeds = await composer.gather_seeds(target_date)
        if not seeds:
            logger.info("[DailyReportAgent] ArticlePool empty, triggering Ingester first")
            try:
                from app.services.ingester import ContinuousIngester
                await ContinuousIngester().run()
                seeds = await composer.gather_seeds(target_date)
            except Exception as exc:
                logger.warning("[DailyReportAgent] Ingester trigger failed: %s", exc)

        if seeds:
            for s in seeds:
                metadata = dict(s.get("metadata") or {})
                discovery = dict(metadata.get("discovery") or {})
                if discovery:
                    metadata.update(discovery)
                if s.get("section"):
                    metadata.setdefault("intended_section", s.get("section"))
                if s.get("category"):
                    metadata.setdefault("intended_category", s.get("category"))
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
                        "language": s.get("language", "zh"),
                        "source_type": s.get("source_type"),
                    },
                })
            if event_queue:
                event_queue.put_nowait({"type": "stats", "phase": "seed", "seed_count": len(seeds)})

        from app.services.zhipu_search import ZhipuSearchClient
        from app.services.tools import (
            CheckCoverageTool,
            CompareSourcesTool,
            EvaluateArticleTool,
            FinishTool,
            ReadPageTool,
            SearchImagesTool,
            VerifyImageTool,
            WebSearchTool,
            WriteSectionTool,
        )

        zhipu = ZhipuSearchClient()
        agent_tools = [
            WebSearchTool(bocha_client=bocha, zhipu_client=zhipu),
            ReadPageTool(scraper_client=scraper, timeout_seconds=runtime["scrape_timeout_seconds"]),
            EvaluateArticleTool(llm_client=llm_client),
            SearchImagesTool(scraper_client=scraper),
            VerifyImageTool(llm_client=llm_client),
            WriteSectionTool(llm_client=llm_client),
            CheckCoverageTool(),
            FinishTool(llm_client=llm_client),
            CompareSourcesTool(llm_client=llm_client),
        ]

        harness = self._build_agent_harness()
        agent = AgentCore(tools=agent_tools, llm_client=llm_client, harness=harness, event_queue=event_queue)
        task = self._build_task_prompt(target_date)
        result = await agent.run(task=task, agent_run_id=agent_run_id, memory=memory)

        logger.info("[DailyReportAgent] Agent finished: %d articles, reason=%s", len(result.articles), result.finished_reason)
        return await self._result_to_report(result, target_date, run_id, agent_run_id, shadow_mode, mode, runtime, llm_client, llm_client)

    # ── Prompt 构建 ───────────────────────────────────────────

    def _build_task_prompt(
        self,
        target_date: date,
        *,
        fallback_candidates: list[tuple[str, str]] | None = None,
        search_enabled: bool = True,
        provider_health: dict[str, str] | None = None,
    ) -> str:
        now = now_local()
        provider_line = ""
        if provider_health:
            provider_line = (
                "当前搜索服务状态："
                + "，".join(
                    f"{name}:{state}" for name, state in provider_health.items()
                )
                + "。\n"
            )
        candidate_block = ""
        if fallback_candidates:
            formatted = "\n".join(
                f"- {url} | {(context or '').splitlines()[0][:80]}"
                for url, context in fallback_candidates[:6]
            )
            candidate_block = (
                "请优先处理以下已发现的候选详情页，不要脱离这些候选去读站点首页、专题页、subject/topic 页面：\n"
                f"{formatted}\n"
            )
        search_line = (
            "若已有候选足够，优先消化候选，不要重复 broad web_search。\n"
            if search_enabled
            else "搜索服务当前不可依赖，禁止做开放式 broad web_search；只允许处理已发现候选。\n"
        )
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请生成今日《{settings.report_title}》（{target_date.isoformat()}）。\n"
            f"时效要求：优先只收录过去{_SEARCH_RECENCY_LABEL}发布的内容。不要在搜索词中加上往年年份。\n"
            f"{provider_line}{search_line}{candidate_block}"
            "严禁把 homepage、navigation、topic、subject、journal landing、频道页、列表页当作正文证据；只阅读具体文章详情页或明确带发布日期的新闻稿。\n"
        )

    # ── 辅助方法 ──────────────────────────────────────────────

    async def _seed_trusted_source_candidates(self, memory: WorkingMemory) -> int:
        from app.utils import canonicalize_url

        try:
            with session_scope() as session:
                sources = list_sources(session)
        except Exception as exc:
            logger.warning(
                "[DailyReportAgent] Failed to load source rules for seeding: %s", exc
            )
            return 0

        seeded = 0
        seen_urls = {
            canonicalize_url(row.get("url", ""))
            for row in memory.search_results
            if row.get("url")
        }
        trusted_sources = [
            source
            for source in sources
            if source.enabled
            and source.rss_or_listing_url
            and (source.use_direct_source or source.crawl_mode == "rss")
        ]
        trusted_sources.sort(
            key=lambda source: (
                _TRUSTED_SOURCE_TIER_RANK.get(str(source.source_tier or "unknown"), 1),
                int(source.priority or 0),
            ),
            reverse=True,
        )
        selected_sources = trusted_sources[:_TRUSTED_SOURCE_SEED_LIMIT]
        if not selected_sources:
            return 0

        feed_results = await asyncio.gather(
            *[
                fetch_feed_entries(
                    str(source.rss_or_listing_url), source.name, source.type
                )
                for source in selected_sources
            ],
            return_exceptions=True,
        )

        for source, result in zip(selected_sources, feed_results, strict=False):
            if isinstance(result, Exception):
                logger.warning(
                    "[DailyReportAgent] Trusted source seed failed for %s: %s",
                    source.domain,
                    result,
                )
                continue
            if not isinstance(result, list):
                continue
            enriched_rows: list[dict[str, Any]] = []
            for row in result[:_TRUSTED_SOURCE_ITEMS_PER_FEED]:
                if not self._seed_row_is_relevant(row):
                    memory.record_candidate_rejection("trusted_seed_off_topic")
                    continue
                normalized_url = canonicalize_url(str(row.get("url") or ""))
                if not normalized_url or normalized_url in seen_urls:
                    continue
                metadata = dict(row.get("metadata") or {})
                metadata.update(
                    {
                        "search_type": "rss",
                        "is_direct_source": bool(source.use_direct_source),
                        "source_priority": int(source.priority or 0),
                        "seeded_from_trusted_source": True,
                    }
                )
                enriched_row = {
                    **row,
                    "search_type": "rss",
                    "source_name": row.get("source_name") or source.name,
                    "source_type": row.get("source_type") or source.type,
                    "metadata": metadata,
                }
                seen_urls.add(normalized_url)
                enriched_rows.append(enriched_row)
            if enriched_rows:
                memory.record_search_results(f"seed:{source.domain}", enriched_rows)
                seeded += len(enriched_rows)

        return seeded

    async def _fetch_article_pool(self, target_date: date) -> list[ArticlePool]:
        from datetime import timedelta
        articles: list[ArticlePool] = []
        try:
            with session_scope() as session:
                since = target_date - timedelta(hours=72)
                articles = list(
                    session.scalars(
                        select(ArticlePool)
                        .where(ArticlePool.ingested_at >= since)
                        .order_by(ArticlePool.ingested_at.desc())
                        .limit(200)
                    ).all()
                )
        except Exception as exc:
            logger.warning("Failed to fetch ArticlePool: %s", exc)
        return articles

    def _seed_row_is_relevant(self, row: dict[str, Any]) -> bool:
        from app.services.source_quality import classify_source

        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        url = str(row.get("url") or "")
        text = f"{title} {snippet}".lower()
        if not any(keyword.lower() in text for keyword in _POSITIVE_KEYWORDS):
            return False
        quality = classify_source(url=url, title=title, content=snippet)
        if quality["page_kind"] in _PREVIEW_REJECT_PAGE_KINDS:
            return False
        if quality["source_tier"] == "D":
            return False
        negative_hits = sum(
            1 for keyword in _NEGATIVE_KEYWORDS if keyword.lower() in text
        )
        positive_hits = sum(
            1 for keyword in _POSITIVE_KEYWORDS if keyword.lower() in text
        )
        return not (negative_hits > 0 and positive_hits == 0)

    @staticmethod
    def _infer_language(domain: str) -> str:
        domain_lower = domain.lower()
        if domain_lower.endswith(".cn") or ".com.cn" in domain_lower:
            return "zh"
        for tld in (".tw", ".hk", ".jp", ".kr"):
            if domain_lower.endswith(tld) or f"{tld}/" in domain_lower:
                return "zh"
        return "zh" if any(kw in domain_lower for kw in ["sina", "sohu", "qq", "163", "36kr"]) else "en"

    @staticmethod
    def _display_source_name(article: dict[str, Any]) -> str:
        from app.services.report_persistence import _display_source_name
        return _display_source_name(article)

    # ── 代理方法（保持测试兼容性）───────────────────────────────

    @staticmethod
    def _auto_publish_status(
        *,
        effective_topic_count: int,
        section_count: int,
        recent_verified_count: int,
        a_tier_count: int,
        article_count: int,
        runtime: dict[str, Any],
    ) -> tuple[str, str]:
        return auto_publish_status(
            effective_topic_count=effective_topic_count,
            section_count=section_count,
            recent_verified_count=recent_verified_count,
            a_tier_count=a_tier_count,
            article_count=article_count,
            runtime=runtime,
        )

    @staticmethod
    def _publish_grade_from_status(status: str) -> str:
        return publish_grade_from_status(status)

    def _extract_candidate_urls(
        self,
        memory: WorkingMemory,
        runtime: dict[str, Any],
        limit: int | None = None,
    ) -> list[tuple[str, str]]:
        return extract_candidates(memory, runtime, limit)

    def _candidate_score(
        self,
        row: dict[str, Any],
        memory: WorkingMemory,
        query_usage: dict[str, int],
        quality: dict[str, Any],
    ) -> float:
        return candidate_score(row, memory, query_usage, quality)

    @staticmethod
    def _candidate_section_hints(row: dict[str, Any]) -> set[str]:
        return candidate_section_hints(row)

    @staticmethod
    def _scrape_failure_rate(memory: WorkingMemory) -> float:
        attempted = len(memory.attempted_urls)
        if attempted < 3:
            return 0.0
        successful = len(memory.read_urls)
        return 1.0 - (successful / attempted)

    @staticmethod
    def _should_disable_fallback_search(memory: WorkingMemory) -> bool:
        provider_health = memory.search_provider_health or {}
        for provider in ["bocha", "zhipu"]:
            snapshot = provider_health.get(provider, {})
            state = str(snapshot.get("health_state") or snapshot.get("state") or "")
            last_error = str(snapshot.get("last_error") or "")
            is_unhealthy = (
                state in {"quota_limited", "circuit_open", "unavailable", "error"}
                or last_error in {"quota_exceeded", "connect_error", "connection_error", "network_error"}
            )
            if not is_unhealthy:
                return False
        return True

    @staticmethod
    def _should_skip_fallback_for_scrape_health(memory: WorkingMemory) -> bool:
        attempted = len(memory.attempted_urls)
        if attempted < 3:
            return False
        rate = 1.0 - (len(memory.read_urls) / attempted)
        return rate > 0.8

    async def _result_to_report(
        self,
        result: AgentResult,
        target_date: date,
        run_id: int,
        agent_run_id: int,
        shadow_mode: bool | None,
        mode: str,
        runtime: dict[str, Any],
        llm_client: LLMClient,
        synthesis_llm_client: LLMClient,
    ) -> Report:
        return await result_to_report(
            result, target_date, run_id, agent_run_id,
            shadow_mode, mode, runtime, llm_client, synthesis_llm_client,
        )
