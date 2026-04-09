import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.config import settings
from app.models import AgentRun, Report, ReportItem, RetrievalRun
from app.services.agent_core import AgentCore, AgentResult
from app.services.article_agent import ArticleAgent, ArticleCard, ArticleHarness
from app.services.brave import BraveSearchClient
from app.services.jina_reader import JinaReaderClient
from app.services.scraper import ScraperClient
from app.services.zhipu_search import ZhipuSearchClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.repository import get_report_settings
from app.services.source_quality import SOURCE_TIER_RANK, classify_source
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
    build_all_tools,
)
from app.services.working_memory import WorkingMemory
from app.utils import canonicalize_url, extract_domain, normalize_external_url, normalize_title, now_local

logger = logging.getLogger(__name__)

_POSITIVE_KEYWORDS = [
    "高分子", "塑料", "树脂", "改性", "注塑", "挤出", "吹塑", "复合材料",
    "recycling", "polymer", "plastics", "resin", "extrusion", "injection", "processing",
]
_NEGATIVE_KEYWORDS = [
    "market forecast", "cagr", "stock", "earnings", "marathon", "football", "soccer",
    "war", "missile", "ophthalmology", "biogen", "apellis", "pharma",
    "财经", "股价", "财报", "马拉松", "足球", "战争", "导弹", "医药",
]
_SINGLE_DOMAIN_CANDIDATE_CAP = 3
_PREVIEW_REJECT_PAGE_KINDS = {"download", "search", "product", "about", "homepage", "navigation", "anti_bot", "binary"}
_NON_RETRYABLE_READ_STATES = {"readable", "rejected_by_page_kind", "rejected_by_quality", "rejected_by_recency"}

# ── Phase 1: 搜索阶段 System Prompt ─────────────────────────
SEARCH_PHASE_SYSTEM_PROMPT = """\
你是高分子材料加工领域的情报搜索专家。
你的唯一任务是发现有价值的文章链接——不需要阅读文章、不需要写报告。

【必须覆盖的话题维度】
- 设备与技术：注塑机/挤出机新品发布、智能制造升级、3D打印应用
- 原料与市场：树脂/助剂价格行情、供应紧张或过剩、企业并购重组、产能扩建
- 下游应用：汽车轻量化、电子封装、医疗器械、包装食品接触材料
- 政策法规：环保法规（限塑令/碳关税）、行业标准更新、补贴政策
- 国际动向：海外企业动态、贸易摩擦影响、国际展会报道（如K展、Chinaplas）

【来源质量要求】
- 优先选择：行业媒体（PlasticsToday、PlasticsNews、PTOnline、European Plastics News）、
  大陆权威新闻（新浪财经、搜狐、36氪、化工707）、企业官网新闻稿、学术期刊官网
- 排除：B2B电商平台（alibaba、made-in-china、1688、globalsources）、
  纯投资分析站（investing.com 各区域站）、产品比价页面、百科词条
- 发现此类链接时不要收录，直接跳过

【工作流程】
1. 执行至少 8 轮 web_search，中英文各半，每轮覆盖不同子话题
2. 搜索词要具体且多样化——不要反复搜同一主题的近义词
3. 每 3-4 轮用 check_coverage 评估进度，补足缺口
4. 确保每个维度至少有 2 轮搜索覆盖后再调用 finish

注意：你不需要阅读任何页面，后续会有专门的 Agent 处理每篇文章。
"""

# ── Phase 3: 综合阶段 System Prompt ─────────────────────────
SYNTHESIS_PHASE_SYSTEM_PROMPT = """\
你是高分子材料加工日报的总编辑兼首席分析师。
你的目标不是搬运新闻，而是产出一份有深度洞察的行业情报简报。

【工作流程】
1. 调用 compare_sources 做深度对比分析——识别重复、发现趋势、判断关联
2. 调用 check_coverage 确认最终覆盖状态
3. 为每个有文章的板块调用 write_section 撰写深度分析
4. 最后调用 finish 输出完整日报

【报告定位：行业洞察报告，不是新闻聚合】
- 每个主题必须包含：事实陈述 + 行业影响分析/趋势预判
- 如果多篇文章指向同一趋势，合并分析并标注信号强度
- 用数据说话：价格变动幅度、产能规模、技术参数、政策时间线
- 每条分析末尾附来源引用（超链接格式），标注信息可靠度
- 如果某板块只有 1 篇文章，深度展开该条目的行业影响分析
- 如果发现跨板块的关联（如政策变化影响产业链），在分析中指出

当前已有文章和配图状态会在工作记忆中展示。
"""

# ── Fallback: 单体模式 System Prompt ────────────────────────
FALLBACK_SYSTEM_PROMPT = """\
你是高分子材料加工领域的专业情报分析 Agent。
你需要独立完成搜索、阅读、评估和撰写每日行业资讯日报。

【工作流程】
1. 执行 6-8 轮 web_search，覆盖产业/技术/政策维度
2. 阅读有价值的文章并用 evaluate_article 评估
3. 用 check_coverage 检查进度，补足缺口
4. 为有价值文章找配图并验证
5. 调用 write_section 撰写各板块
6. 调用 finish 完成报告
"""

# 兼容 make_daily_report_harness() 等旧调用方。
DAILY_REPORT_SYSTEM_PROMPT = FALLBACK_SYSTEM_PROMPT


class DailyReportAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def _runtime_settings(self, payload: dict[str, Any] | None, shadow_mode: bool | None) -> dict[str, Any]:
        payload = payload or {}
        return {
            "shadow_mode": settings.shadow_mode if shadow_mode is None else shadow_mode,
            "scrape_timeout_seconds": int(payload.get("scrape_timeout_seconds", settings.scrape_timeout_seconds)),
            "scrape_concurrency": max(1, int(payload.get("scrape_concurrency", settings.scrape_concurrency))),
            "max_extractions_per_run": max(1, int(payload.get("max_extractions_per_run", settings.max_extractions_per_run))),
            "domain_failure_threshold": max(1, int(payload.get("domain_failure_threshold", settings.domain_failure_threshold))),
            "report_primary_model": payload.get("report_primary_model", settings.report_primary_model),
            "report_fallback_model": payload.get("report_fallback_model", settings.report_fallback_model),
            "strict_primary_model_for_tool_use": bool(payload.get("strict_primary_model_for_tool_use", settings.strict_primary_model_for_tool_use)),
            "strict_primary_model_for_all_llm": bool(payload.get("strict_primary_model_for_all_llm", settings.strict_primary_model_for_all_llm)),
            "tool_use_fallback_mode": payload.get("tool_use_fallback_mode", settings.tool_use_fallback_mode),
            "report_min_formal_topics": max(1, int(payload.get("report_min_formal_topics", settings.report_min_formal_topics))),
            "report_target_items": max(1, int(payload.get("report_target_items", settings.report_target_items))),
        }

    def _build_runtime_llm_client(self, runtime: dict[str, Any]) -> LLMClient:
        return LLMClient(
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            timeout=self._llm_client.timeout,
            strict_primary_model_for_tool_use=runtime["strict_primary_model_for_tool_use"],
            strict_primary_model_for_all_llm=runtime["strict_primary_model_for_all_llm"],
            tool_use_fallback_mode=runtime["tool_use_fallback_mode"],
        )

    def _build_synthesis_llm_client(self, runtime: dict[str, Any]) -> LLMClient:
        return LLMClient(
            primary_model=runtime["report_primary_model"],
            fallback_model=runtime["report_fallback_model"],
            timeout=self._llm_client.timeout,
            strict_primary_model_for_tool_use=runtime["strict_primary_model_for_tool_use"],
            strict_primary_model_for_all_llm=runtime["strict_primary_model_for_all_llm"],
            tool_use_fallback_mode=runtime["tool_use_fallback_mode"],
        )

    # ── Harness Presets ──────────────────────────────────────

    def _build_search_harness(self) -> Harness:
        """Phase 1: 搜索阶段。只搜索不阅读。"""
        return Harness(
            max_steps=30,
            max_search_calls=24,
            max_page_reads=0,
            max_llm_calls=25,
            max_duration_seconds=480.0,
            min_searches_before_finish=10,
            min_articles_before_finish=0,
            system_prompt=SEARCH_PHASE_SYSTEM_PROMPT,
        )

    def _build_synthesis_harness(self) -> Harness:
        """Phase 3: 综合阶段。只去重、撰写、完成。"""
        return Harness(
            max_steps=15,
            max_search_calls=0,
            max_page_reads=0,
            max_llm_calls=12,
            max_duration_seconds=300.0,
            min_searches_before_finish=0,
            min_articles_before_finish=0,
            system_prompt=SYNTHESIS_PHASE_SYSTEM_PROMPT,
        )

    def _build_fallback_harness(self) -> Harness:
        """Fallback: 单体模式。全工具集。"""
        return Harness(
            max_steps=60,
            max_search_calls=20,
            max_page_reads=20,
            max_llm_calls=50,
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

        # ── Phase A: 创建 DB 记录（短 session，<1s）──
        if run_id is None:
            # Caller didn't pre-create a RetrievalRun — create both records
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
            # Caller already created the RetrievalRun — only create AgentRun
            with _session_scope() as session:
                agent_run = AgentRun(
                    retrieval_run_id=run_id,
                    agent_type="daily_report_v2",
                )
                session.add(agent_run)
                session.flush()
                session.commit()
                agent_run_id = agent_run.id

        # ── Phase B: 异步 I/O（无 session，15-25 min）──
        try:
            report = await self._run_phases(
                target_date, run_id, agent_run_id, shadow_mode, mode, event_queue, runtime,
            )
        except Exception as exc:
            logger.error("[DailyReportAgent] Pipeline failed: %s", exc, exc_info=True)
            # 用短 session 标记失败
            try:
                with _session_scope() as session:
                    run_obj = session.get(RetrievalRun, run_id)
                    ar_obj = session.get(AgentRun, agent_run_id)
                    if run_obj:
                        run_obj.status = "failed"
                        run_obj.error_message = str(exc)[:500]
                        run_obj.finished_at = now_local()
                        run_obj.debug_payload = {
                            **(run_obj.debug_payload or {}),
                            "runtime": runtime,
                        }
                    if ar_obj:
                        ar_obj.status = "failed"
                        ar_obj.finished_reason = "error"
            except Exception:
                logger.error("[DailyReportAgent] Also failed to update status", exc_info=True)
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
        """Phase B: 所有异步 I/O 操作，不持有任何 DB session。"""
        # 初始化共享资源
        brave = BraveSearchClient()
        jina = JinaReaderClient()
        scraper = ScraperClient(jina_client=jina)
        zhipu = ZhipuSearchClient()
        memory = WorkingMemory()
        llm_client = self._build_runtime_llm_client(runtime)
        synthesis_llm_client = self._build_synthesis_llm_client(runtime)

        # ============ Phase 1: 搜索发现 ============
        logger.info("[DailyReportAgent] Phase 1: Search Discovery")
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 1, "name": "搜索发现"})
        search_tools: list = [
            WebSearchTool(brave_client=brave, zhipu_client=zhipu),
            CheckCoverageTool(),
            FinishTool(),
        ]
        search_harness = self._build_search_harness()
        search_agent = AgentCore(
            tools=search_tools, llm_client=llm_client, harness=search_harness,
            event_queue=event_queue,
        )

        search_prompt = self._build_search_prompt(target_date)
        search_result = await search_agent.run(
            task=search_prompt, agent_run_id=agent_run_id, memory=memory,
        )
        logger.info(
            "[DailyReportAgent] Phase 1 done: %d search results, %d queries, reason=%s",
            len(memory.search_results), len(memory.searched_queries), search_result.finished_reason,
        )
        if event_queue:
            event_queue.put_nowait({
                "type": "stats",
                "phase": 1,
                "query_count": len(memory.searched_queries),
                "search_result_count": len(memory.search_results),
                "image_search_result_count": len(memory.image_search_results),
            })

        # ============ Phase 2: 并发文章处理 ============
        logger.info("[DailyReportAgent] Phase 2: Parallel Article Processing")
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 2, "name": "文章处理"})
        candidate_urls = self._extract_candidate_urls(memory, runtime)

        if not candidate_urls:
            logger.warning("[DailyReportAgent] No candidate URLs found, falling back to monolithic mode")
            return await self._run_fallback(
                target_date, run_id, agent_run_id, brave, scraper, memory, shadow_mode, mode, runtime, llm_client,
            )

        # 构建 sub-agent 工具
        article_tools = {
            "read_page": ReadPageTool(scraper_client=scraper, timeout_seconds=runtime["scrape_timeout_seconds"]),
            "evaluate_article": EvaluateArticleTool(llm_client=llm_client),
            "search_images": SearchImagesTool(brave_client=brave, scraper_client=scraper),
            "verify_image": VerifyImageTool(llm_client=llm_client),
        }

        # 创建并运行 Article Agents
        article_agents = [
            ArticleAgent(
                url=url, context=context, memory=memory,
                tools=article_tools, harness=ArticleHarness(),
                agent_run_id=agent_run_id,
            )
            for url, context in candidate_urls
        ]
        cards = await self._run_article_agents(
            article_agents,
            memory=memory,
            max_concurrency=runtime["scrape_concurrency"],
            domain_failure_threshold=runtime["domain_failure_threshold"],
        )
        supervisor_actions: list[dict[str, Any]] = []
        supplement_candidates_found = 0
        supplement_agents_launched = 0
        supplement_successful_articles = 0
        total_attempted_articles = len(cards)

        successful = [c for c in cards if c.success and c.section != "rejected"]
        phase2_rejected_missing_date_count = sum(
            1 for c in cards if c.section == "rejected" and "发布时间缺失" in (c.evaluation_reason or "")
        )
        phase2_rejected_stale_count = sum(
            1 for c in cards if c.section == "rejected" and "发布时间过旧" in (c.evaluation_reason or "")
        )
        phase2_soft_accepted_unknown_date_count = sum(
            1 for a in memory.publishable_articles() if getattr(a, "recency_status", "") == "unknown"
        )
        logger.info(
            "[DailyReportAgent] Phase 2 done: %d/%d articles processed successfully",
            len(successful), len(cards),
        )
        if event_queue:
            event_queue.put_nowait({
                "type": "stats",
                "phase": 2,
                "candidate_count": len(candidate_urls),
                "successful_articles": len(successful),
                "scrape_layer_stats": memory.scrape_layer_stats,
            })

        # ============ Phase 2.5 (A): 补充搜索 / supervisor loop ============
        supplement_round = 0
        while self._should_run_supervisor_round(memory, successful, supplement_round, runtime):
            supplement_round += 1
            action = self._build_supervisor_action(memory, successful, supplement_round)
            supervisor_actions.append(action)
            logger.info(
                "[DailyReportAgent] Supervisor round %d triggered: %s",
                supplement_round,
                action["reason"],
            )
            supplement_harness = Harness(
                max_steps=12,
                max_search_calls=8,
                max_page_reads=0,
                max_llm_calls=10,
                max_duration_seconds=180.0,
                min_searches_before_finish=4,
                min_articles_before_finish=0,
                system_prompt=SEARCH_PHASE_SYSTEM_PROMPT,
            )
            supplement_prompt = self._build_supplement_search_prompt(target_date, memory)
            supplement_agent = AgentCore(
                tools=search_tools, llm_client=llm_client, harness=supplement_harness,
            )
            await supplement_agent.run(
                task=supplement_prompt, agent_run_id=agent_run_id, memory=memory,
            )
            # 提取新候选 URL，按已成功产出的条目数量而不是首轮候选数计算剩余额度
            processed_success_count = len(successful)
            remaining_slots = max(0, runtime["max_extractions_per_run"] - processed_success_count)
            new_candidates = self._extract_candidate_urls(memory, runtime, limit=remaining_slots)
            supplement_candidates_found += len(new_candidates)
            if new_candidates and remaining_slots > 0:
                launched_this_round = min(len(new_candidates), remaining_slots)
                supplement_agents_launched += launched_this_round
                logger.info(
                    "[DailyReportAgent] Supplement search round %d found %d new candidates, launching %d agents",
                    supplement_round,
                    len(new_candidates),
                    launched_this_round,
                )
                new_agents = [
                    ArticleAgent(
                        url=url, context=context, memory=memory,
                        tools=article_tools, harness=ArticleHarness(),
                        agent_run_id=agent_run_id,
                    )
                    for url, context in new_candidates[:remaining_slots]
                ]
                new_cards = await self._run_article_agents(
                    new_agents,
                    memory=memory,
                    max_concurrency=runtime["scrape_concurrency"],
                    domain_failure_threshold=runtime["domain_failure_threshold"],
                )
                total_attempted_articles += len(new_cards)
                phase2_rejected_missing_date_count += sum(
                    1 for c in new_cards if c.section == "rejected" and "发布时间缺失" in (c.evaluation_reason or "")
                )
                phase2_rejected_stale_count += sum(
                    1 for c in new_cards if c.section == "rejected" and "发布时间过旧" in (c.evaluation_reason or "")
                )
                new_successful = [c for c in new_cards if c.success and c.section != "rejected"]
                supplement_successful_articles += len(new_successful)
                successful.extend(new_successful)
                phase2_soft_accepted_unknown_date_count = sum(
                    1 for a in memory.publishable_articles() if getattr(a, "recency_status", "") == "unknown"
                )
                logger.info(
                    "[DailyReportAgent] After supervisor round %d: %d total successful articles",
                    supplement_round,
                    len(successful),
                )
            else:
                logger.info("[DailyReportAgent] Supervisor round %d found no launchable candidates", supplement_round)
                break

        if not successful:
            logger.warning("[DailyReportAgent] All article agents failed, falling back")
            return await self._run_fallback(
                target_date, run_id, agent_run_id, brave, scraper, memory, shadow_mode, mode, runtime, llm_client,
            )

        # ============ Phase 2.5: 链接可用性验证 ============
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 2.5, "name": "链接验证"})
        logger.info("[DailyReportAgent] Phase 2.5: Link Validation (%d articles)", len(successful))
        from app.services.link_checker import LinkChecker
        checker = LinkChecker()
        article_urls = [normalize_external_url(c.url) for c in successful]
        image_urls = [normalize_external_url(c.image_url) for c in successful if c.image_url]
        all_check_urls = article_urls + image_urls
        check_results = await checker.check_batch(all_check_urls)
        url_status = {r.url: r for r in check_results}

        valid_cards: list[ArticleCard] = []
        for card in successful:
            card.url = normalize_external_url(card.url)
            if card.resolved_url:
                card.resolved_url = normalize_external_url(card.resolved_url)
            if card.image_url:
                card.image_url = normalize_external_url(card.image_url)
            result = url_status.get(card.url)
            if result and not result.is_available:
                read_meta = memory.get_read_metadata(card.url)
                if read_meta.get("content_available"):
                    logger.info("Link advisory only for readable article: %s", card.url)
                    if result.redirect_url:
                        card.resolved_url = result.redirect_url
                    valid_cards.append(card)
                    continue
                logger.warning("Link unavailable, removing: %s (status=%s, error=%s)",
                               card.url, result.status_code, result.error)
                continue
            # 检查 image_url
            if card.image_url:
                img_result = url_status.get(card.image_url)
                if img_result and not img_result.is_available:
                    logger.info("Image link unavailable, clearing: %s", card.image_url)
                    card.image_url = None
                    card.image_caption = None
            if result and result.redirect_url:
                card.resolved_url = result.redirect_url
            valid_cards.append(card)

        removed = len(successful) - len(valid_cards)
        if removed:
            logger.info("[DailyReportAgent] Link check removed %d articles, %d remain", removed, len(valid_cards))
        successful = valid_cards

        # 将链接校验后的最终卡片状态（尤其是图片 URL）回写到 WorkingMemory。
        # Phase 3 的 FinishTool 读取的是 memory.publishable_articles()，若不回写，
        # 会出现日志里 image=yes 但最终报告与数据库中 image_url 为空的错位。
        for card in successful:
            memory.sync_article_card(card)

        if not successful:
            logger.warning("[DailyReportAgent] All links failed validation, falling back")
            return await self._run_fallback(
                target_date, run_id, agent_run_id, brave, scraper, memory, shadow_mode, mode, runtime, llm_client,
            )

        compiled_topics = self._compile_section_topics(memory, runtime)
        if event_queue:
            event_queue.put_nowait({
                "type": "stats",
                "phase": 2.6,
                "compiled_sections": {section: len(topics) for section, topics in compiled_topics.items()},
            })

        # ============ Phase 3: 编排综合 ============
        logger.info("[DailyReportAgent] Phase 3: Synthesis")
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 3, "name": "编排综合", "article_count": len(successful)})
        final_result = await self._run_deterministic_synthesis(
            memory=memory,
            target_date=target_date,
            llm_client=synthesis_llm_client,
            event_queue=event_queue,
            runtime=runtime,
        )
        final_result.diagnostics.update({
            "supplement_candidates_found": supplement_candidates_found,
            "supplement_agents_launched": supplement_agents_launched,
            "supplement_successful_articles": supplement_successful_articles,
            "phase2_attempted_articles": total_attempted_articles,
            "phase2_successful_articles": len(successful),
            "phase2_rejected_missing_date_count": phase2_rejected_missing_date_count,
            "phase2_rejected_stale_count": phase2_rejected_stale_count,
            "phase2_soft_accepted_unknown_date_count": phase2_soft_accepted_unknown_date_count,
            "supervisor_actions": supervisor_actions,
        })
        logger.info(
            "[DailyReportAgent] Phase 3 done: %d articles, reason=%s",
            len(final_result.articles), final_result.finished_reason,
        )
        if event_queue:
            event_queue.put_nowait({
                "type": "stats",
                "phase": 3,
                "article_count": len(final_result.articles),
                "finished_reason": final_result.finished_reason,
                "phase3_compare_status": final_result.diagnostics.get("phase3_compare_status", {}),
                "phase3_section_results": final_result.diagnostics.get("phase3_section_results", {}),
            })

        # ── Phase C: 持久化 Report（短 session，<1s）──
        return await self._result_to_report(
            final_result, target_date, run_id, agent_run_id, shadow_mode, mode, runtime, llm_client, synthesis_llm_client,
        )

    # ── Fallback: 单体模式 ────────────────────────────────────

    async def _run_fallback(
        self,
        target_date: date,
        run_id: int,
        agent_run_id: int,
        brave: BraveSearchClient,
        scraper: ScraperClient,
        memory: WorkingMemory,
        shadow_mode: bool | None,
        mode: str,
        runtime: dict[str, Any],
        llm_client: LLMClient,
    ) -> Report:
        """所有 Article Agent 失败时，回退到单体 AgentCore 模式。"""
        logger.info("[DailyReportAgent] Running fallback monolithic agent")
        zhipu = ZhipuSearchClient()
        all_tools = build_all_tools(
            brave_client=brave,
            scraper_client=scraper,
            zhipu_client=zhipu,
            llm_client=llm_client,
            scrape_timeout_seconds=runtime["scrape_timeout_seconds"],
        )
        harness = self._build_fallback_harness()
        agent = AgentCore(tools=all_tools, llm_client=llm_client, harness=harness)
        task = self._build_task_prompt(target_date)
        result = await agent.run(task=task, agent_run_id=agent_run_id, memory=memory)
        return await self._result_to_report(result, target_date, run_id, agent_run_id, shadow_mode, mode, runtime, llm_client, llm_client)

    # ── Article Agent 并发运行 ────────────────────────────────

    async def _run_article_agents(
        self,
        agents: list[ArticleAgent],
        memory: WorkingMemory,
        max_concurrency: int = 5,
        domain_failure_threshold: int = 2,
    ) -> list[ArticleCard]:
        """批次化运行 Article Agents，并在批次之间执行域名失败熔断。"""
        results: list[ArticleCard] = []
        failure_counts: dict[str, int] = defaultdict(int)

        async def run_one(agent: ArticleAgent) -> ArticleCard:
            try:
                return await asyncio.wait_for(
                    agent.run(),
                    timeout=agent.harness.max_duration_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning("[ArticleAgent] Timeout for %s", agent.url[:60])
                return ArticleCard(
                    url=agent.url, title="", domain=extract_domain(agent.url), source_name="",
                    published_at=None, summary="", section="rejected",
                    key_finding="", success=False, error="timeout",
                )
            except Exception as exc:
                logger.error("[ArticleAgent] Error for %s: %s", agent.url[:60], exc)
                return ArticleCard(
                    url=agent.url, title="", domain=extract_domain(agent.url), source_name="",
                    published_at=None, summary="", section="rejected",
                    key_finding="", success=False, error=str(exc),
                )

        for index in range(0, len(agents), max_concurrency):
            batch: list[ArticleAgent] = []
            for agent in agents[index:index + max_concurrency]:
                domain = extract_domain(agent.url)
                if failure_counts[domain] >= domain_failure_threshold:
                    memory.record_candidate_rejection("domain_circuit_breaker")
                    results.append(
                        ArticleCard(
                            url=agent.url,
                            title="",
                            domain=domain,
                            source_name="",
                            published_at=None,
                            summary="",
                            section="rejected",
                            key_finding="",
                            success=False,
                            error="domain_circuit_breaker",
                        )
                    )
                    continue
                batch.append(agent)

            if not batch:
                continue

            batch_results = await asyncio.gather(*[run_one(agent) for agent in batch])
            for card in batch_results:
                if not card.success:
                    domain = card.domain or extract_domain(card.url)
                    failure_counts[domain] += 1
                    memory.record_domain_failure(domain, card.error or "article_agent_failed")
                results.append(card)

        return results

    async def _run_deterministic_synthesis(
        self,
        memory: WorkingMemory,
        target_date: date,
        llm_client: LLMClient | None,
        event_queue: asyncio.Queue | None,
        runtime: dict[str, Any],
    ) -> AgentResult:
        """确定性编排综合阶段，避免 Phase 3 再跑长上下文 tool-use。"""
        started = time.time()
        compare_tool = CompareSourcesTool(llm_client=llm_client)
        write_tool = WriteSectionTool(llm_client=llm_client)
        finish_tool = FinishTool(llm_client=llm_client)

        compare_status: dict[str, Any] = {"status": "skipped", "reason": "not_enough_articles"}
        section_results: dict[str, dict[str, Any]] = {}

        publishable = memory.publishable_articles()
        if len(publishable) >= 2:
            try:
                compare_result = await asyncio.wait_for(
                    compare_tool.execute(memory=memory, focus="辅助去重与趋势说明"),
                    timeout=12.0,
                )
                compare_status = {
                    "status": "ok" if compare_result.success else "failed",
                    "summary": compare_result.summary[:240],
                    "trend_count": len(compare_result.data.get("trends", [])),
                }
            except asyncio.TimeoutError:
                compare_status = {"status": "timeout", "reason": "compare_sources_timeout"}
                if event_queue:
                    event_queue.put_nowait({
                        "type": "warning",
                        "warning_code": "phase3_compare_timeout",
                        "message": "compare_sources 超时，已跳过并继续写作。",
                    })
            except Exception as exc:
                compare_status = {
                    "status": "failed",
                    "reason": f"{exc.__class__.__name__}: {str(exc)[:160]}".strip(),
                }
                if event_queue:
                    event_queue.put_nowait({
                        "type": "warning",
                        "warning_code": "phase3_compare_failed",
                        "message": f"compare_sources 失败，已跳过：{exc.__class__.__name__}",
                    })

        target_count = max(1, min(runtime.get("report_target_items", settings.report_target_items), settings.max_items_per_section))
        for section in ["industry", "policy", "academic"]:
            topics = memory.get_compiled_topics(section)
            if not topics:
                section_results[section] = {"status": "skipped", "topic_count": 0}
                continue

            section_start = time.time()
            try:
                result = await asyncio.wait_for(
                    write_tool.execute(memory=memory, section=section, target_count=target_count),
                    timeout=35.0,
                )
                section_results[section] = {
                    "status": "ok" if result.success else "failed",
                    "topic_count": len(topics[:target_count]),
                    "duration_seconds": round(time.time() - section_start, 2),
                    "generation_mode": memory.section_generation_mode.get(section, "template_fallback"),
                }
            except asyncio.TimeoutError:
                heading = write_tool._SECTION_HEADINGS.get(section, f"## {section}")
                content = write_tool._render_safe_section_template(heading, topics[:target_count])
                memory.cache_section_content(section, content)
                memory.record_section_generation(section, "template_fallback", timed_out=True)
                section_results[section] = {
                    "status": "timeout",
                    "topic_count": len(topics[:target_count]),
                    "duration_seconds": round(time.time() - section_start, 2),
                    "generation_mode": "template_fallback",
                }
                if event_queue:
                    event_queue.put_nowait({
                        "type": "warning",
                        "warning_code": "phase3_write_timeout",
                        "message": f"{section} 板块写作超时，已使用模板降级。",
                    })
            except Exception as exc:
                heading = write_tool._SECTION_HEADINGS.get(section, f"## {section}")
                content = write_tool._render_safe_section_template(heading, topics[:target_count])
                memory.cache_section_content(section, content)
                memory.record_section_generation(section, "template_fallback")
                section_results[section] = {
                    "status": "failed",
                    "topic_count": len(topics[:target_count]),
                    "duration_seconds": round(time.time() - section_start, 2),
                    "generation_mode": "template_fallback",
                    "reason": f"{exc.__class__.__name__}: {str(exc)[:160]}".strip(),
                }
                if event_queue:
                    event_queue.put_nowait({
                        "type": "warning",
                        "warning_code": "phase3_write_failed",
                        "message": f"{section} 板块写作失败，已使用模板降级。",
                    })

        sections_content = memory.get_all_sections_content()
        summary = self._build_final_summary(memory, compare_status)
        finish_result = await finish_tool.execute(
            memory=memory,
            title=f"{settings.report_title}（{target_date.isoformat()}）",
            summary=summary,
            sections_content=sections_content,
        )

        if not finish_result.success:
            return AgentResult(
                success=False,
                title=f"{settings.report_title}（{target_date.isoformat()}）",
                summary=finish_result.summary,
                articles=[a.to_dict() for a in memory.publishable_articles()],
                sections_content=sections_content,
                memory_snapshot=memory.snapshot(),
                harness_status={"phase": "deterministic_synthesis"},
                finished_reason="error",
                diagnostics={
                    "phase3_compare_status": compare_status,
                    "phase3_section_results": section_results,
                    "phase3_total_duration_seconds": round(time.time() - started, 2),
                },
            )

        return AgentResult(
            success=True,
            title=finish_result.data.get("title", f"{settings.report_title}（{target_date.isoformat()}）"),
            summary=finish_result.data.get("summary", summary),
            articles=finish_result.data.get("articles") or [a.to_dict() for a in memory.publishable_articles()],
            sections_content=sections_content,
            editorial=finish_result.data.get("editorial", ""),
            memory_snapshot=memory.snapshot(),
            harness_status={"phase": "deterministic_synthesis"},
            finished_reason="finish_tool",
            diagnostics={
                "phase3_compare_status": compare_status,
                "phase3_section_results": section_results,
                "phase3_total_duration_seconds": round(time.time() - started, 2),
            },
        )

    @staticmethod
    def _build_final_summary(memory: WorkingMemory, compare_status: dict[str, Any]) -> str:
        topics = []
        for section in ["industry", "policy", "academic"]:
            topics.extend(memory.get_compiled_topics(section))
        titles = [topic["title"] for topic in topics[:3]]
        summary = f"本期筛选出 {len(topics)} 个正式主题"
        if titles:
            summary += "，重点包括：" + "；".join(titles)
        if compare_status.get("status") == "ok" and memory.key_findings:
            summary += "。辅助分析提示：" + "；".join(memory.key_findings[:2])
        return summary + "。"

    def _compile_section_topics(self, memory: WorkingMemory, runtime: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
        for article in memory.publishable_articles():
            topic_key = self._topic_key(article)
            grouped[article.section][topic_key].append(article)

        formal_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        provisional_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        excluded_reasons: dict[str, str] = {}
        for section, topics in grouped.items():
            for _, articles in topics.items():
                ordered = sorted(articles, key=self._article_priority, reverse=True)
                primary = [article for article in ordered if article.source_tier in {"A", "B"}]
                supporting = [article for article in ordered if article.source_tier == "C"]
                provisional_candidates = [article for article in ordered if self._is_provisional_candidate(article)]
                if primary:
                    chosen = primary[:2]
                    if supporting:
                        chosen.append(supporting[0])

                    lead = chosen[0]
                    selection_reason = (
                        f"正式主题入选：至少包含 1 条 {lead.source_tier} 级主证据，"
                        f"主题聚焦 {self._topic_key(lead)}，证据强度 {lead.evidence_strength}"
                    )
                    formal_buckets[section].append(self._build_topic_payload(
                        lead=lead,
                        chosen=chosen,
                        section=section,
                        selection_reason=selection_reason,
                        topic_confidence="formal",
                    ))
                    continue

                if provisional_candidates:
                    chosen = provisional_candidates[:2]
                    lead = chosen[0]
                    selection_reason = (
                        f"补位主题入选：当前缺少 A/B 级主证据，"
                        f"基于高相关且近期/时效未知但可用的 {lead.source_tier} 级条目补位"
                    )
                    provisional_buckets[section].append(self._build_topic_payload(
                        lead=lead,
                        chosen=chosen,
                        section=section,
                        selection_reason=selection_reason,
                        topic_confidence="provisional",
                    ))
                    continue

                for article in ordered:
                    excluded_reasons[article.url] = "该主题缺少可用主证据且不满足 provisional 补位条件"

        target_items = runtime.get("report_target_items", settings.report_target_items)
        formal_section_order = sorted(
            ((section, sorted(topics, key=self._topic_score, reverse=True)) for section, topics in formal_buckets.items()),
            key=lambda item: self._topic_score(item[1][0]) if item[1] else 0.0,
            reverse=True,
        )

        selected_topics: list[dict[str, Any]] = []
        for section, topics in formal_section_order:
            if len(selected_topics) >= target_items:
                break
            if topics:
                selected_topics.append(topics.pop(0))

        remaining_formal_topics = sorted(
            [topic for _, topics in formal_section_order for topic in topics],
            key=self._topic_score,
            reverse=True,
        )
        for topic in remaining_formal_topics:
            if len(selected_topics) >= target_items:
                break
            selected_topics.append(topic)

        formal_count = len(selected_topics)
        selected_sections = {topic["section"] for topic in selected_topics}
        provisional_section_order = sorted(
            ((section, sorted(topics, key=self._topic_score, reverse=True)) for section, topics in provisional_buckets.items()),
            key=lambda item: self._topic_score(item[1][0]) if item[1] else 0.0,
            reverse=True,
        )
        if formal_count < runtime.get("report_min_formal_topics", settings.report_min_formal_topics) or len(selected_sections) < 2:
            for section, topics in provisional_section_order:
                if len(selected_topics) >= target_items:
                    break
                if topics and section not in selected_sections:
                    selected_topics.append(topics.pop(0))
                    selected_sections.add(section)

        remaining_provisional_topics = sorted(
            [topic for _, topics in provisional_section_order for topic in topics],
            key=self._topic_score,
            reverse=True,
        )
        for topic in remaining_provisional_topics:
            if len(selected_topics) >= target_items:
                break
            selected_topics.append(topic)

        compiled: dict[str, list[dict[str, Any]]] = {"industry": [], "policy": [], "academic": []}
        selected_urls: set[str] = set()
        selected_topic_count = len(selected_topics)
        provisional_topic_count = sum(1 for topic in selected_topics if topic.get("topic_confidence") == "provisional")
        for topic in selected_topics:
            section = topic["section"]
            clean_topic = {k: v for k, v in topic.items() if k != "articles"}
            compiled.setdefault(section, []).append(clean_topic)
            for article in topic["articles"]:
                selected_urls.add(article.url)
                role = "主证据" if article.source_tier in {"A", "B"} else "辅助证据"
                article.selection_reason = f"{topic['selection_reason']}；该来源作为{role}引用。"
                article.topic_confidence = topic.get("topic_confidence", "")
                article.excluded_reason = ""

        for section in ["industry", "policy", "academic"]:
            memory.cache_compiled_topics(section, compiled.get(section, []))

        for article in list(memory.publishable_articles()):
            if article.url not in selected_urls:
                article.worth_publishing = False
                article.selection_reason = ""
                article.topic_confidence = ""
                article.excluded_reason = excluded_reasons.get(article.url, "未进入最终主题集合（主题压缩或优先级较低）")
        memory.set_formal_topic_count(selected_topic_count)
        memory.rebuild_coverage()

        return compiled

    @staticmethod
    def _build_topic_payload(
        *,
        lead: Any,
        chosen: list[Any],
        section: str,
        selection_reason: str,
        topic_confidence: str,
    ) -> dict[str, Any]:
        return {
            "topic_key": DailyReportAgent._topic_key(lead),
            "title": lead.key_finding or lead.title,
            "section": section,
            "facts": [DailyReportAgent._primary_fact(article.summary) for article in chosen],
            "citations": [
                {
                    "title": article.title,
                    "url": article.resolved_url or article.url,
                    "domain": article.domain,
                    "source_tier": article.source_tier,
                    "source_reliability_label": article.source_reliability_label,
                }
                for article in chosen
            ],
            "source_tier": lead.source_tier,
            "source_reliability_label": lead.source_reliability_label,
            "source_kind": lead.source_kind,
            "page_kind": lead.page_kind,
            "evidence_strength": lead.evidence_strength,
            "supports_numeric_claims": any(article.supports_numeric_claims for article in chosen),
            "allowed_for_trend_summary": any(article.allowed_for_trend_summary for article in chosen),
            "is_primary_source": any(article.is_primary_source for article in chosen),
            "observation_only": False,
            "selection_reason": selection_reason,
            "topic_confidence": topic_confidence,
            "articles": chosen,
        }

    @staticmethod
    def _is_provisional_candidate(article: Any) -> bool:
        if article.source_tier != "C":
            return False
        if article.recency_status == "stale_verified":
            return False
        if article.page_kind in {"download", "search", "navigation", "product", "about", "homepage", "anti_bot"}:
            return False
        lower_reason = (article.evaluation_reason or "").lower()
        reject_markers = ["软文", "电商", "b2b", "弱相关", "聚合", "广告", "商业黄页", "导购", "推广"]
        return not any(marker in lower_reason for marker in reject_markers)

    @staticmethod
    def _article_priority(article: Any) -> tuple[int, int, int]:
        return (
            SOURCE_TIER_RANK.get(article.source_tier, 1),
            1 if article.supports_numeric_claims else 0,
            1 if article.is_primary_source else 0,
        )

    @staticmethod
    def _topic_score(topic: dict[str, Any]) -> float:
        return (
            float(SOURCE_TIER_RANK.get(topic.get("source_tier", "C"), 1)) * 10.0
            + (3.0 if topic.get("supports_numeric_claims") else 0.0)
            + (2.0 if topic.get("is_primary_source") else 0.0)
            + (1.0 if topic.get("allowed_for_trend_summary") else 0.0)
        )

    @staticmethod
    def _topic_key(article: Any) -> str:
        text = f"{article.title} {article.key_finding} {article.summary}".lower()
        topic_keywords = {
            "exhibition": ["chinaplas", "k show", "展会", "博览会"],
            "equipment": ["注塑", "挤出", "设备", "equipment", "machine"],
            "raw_materials": ["pp", "pe", "pvc", "树脂", "原料", "价格", "行情"],
            "policy": ["政策", "法规", "标准", "gb", "cbam", "碳关税"],
            "recycling": ["回收", "再生", "bioplastic", "化学回收", "recycling"],
            "academic": ["研究", "论文", "study", "journal", "4d打印", "4d printing"],
            "semiconductor": ["半导体", "封装", "环氧", "emc"],
        }
        for key, keywords in topic_keywords.items():
            if any(keyword in text for keyword in keywords):
                return key
        return normalize_title(article.key_finding or article.title)[:48] or article.url

    @staticmethod
    def _primary_fact(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        for delimiter in ["。", ".", ";", "；"]:
            if delimiter in normalized:
                part = normalized.split(delimiter, 1)[0].strip()
                if part:
                    return part
        return normalized[:120]

    # ── 候选 URL 提取 ─────────────────────────────────────────

    def _extract_candidate_urls(
        self,
        memory: WorkingMemory,
        runtime: dict[str, Any],
        limit: int | None = None,
    ) -> list[tuple[str, str]]:
        """从 memory.search_results 中提取、排序、去重候选 URL。"""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        domain_counts: dict[str, int] = defaultdict(int)
        query_usage: dict[str, int] = defaultdict(int)
        scored: list[tuple[float, str, str]] = []
        limit = limit if limit is not None else runtime["max_extractions_per_run"]

        for row in memory.search_results:
            url = row.get("url", "")
            if not url:
                memory.record_candidate_rejection("missing_url")
                continue

            normalized_url = canonicalize_url(url)
            if memory.has_read(normalized_url):
                memory.record_candidate_rejection("already_read")
                continue
            if memory.has_attempted_read(normalized_url):
                read_state = memory.get_read_metadata(normalized_url).get("read_state", "")
                if read_state in _NON_RETRYABLE_READ_STATES:
                    memory.record_candidate_rejection("already_attempted_non_retryable")
                    continue
            if any(ext in normalized_url.lower() for ext in [".pdf", ".jpg", ".png", ".gif"]):
                memory.record_candidate_rejection("unsupported_extension")
                continue
            if normalized_url in seen_urls:
                memory.record_candidate_rejection("duplicate_url")
                continue

            title = row.get("title", "")
            title_key = normalize_title(title)
            if title_key and title_key in seen_titles:
                memory.record_candidate_rejection("duplicate_title")
                continue

            domain = row.get("domain") or extract_domain(url)
            if domain_counts[domain] >= _SINGLE_DOMAIN_CANDIDATE_CAP:
                memory.record_candidate_rejection("domain_candidate_cap")
                continue

            quality = classify_source(url=url, title=title, content=row.get("snippet", ""))
            if quality["page_kind"] in _PREVIEW_REJECT_PAGE_KINDS:
                memory.record_candidate_rejection(f"page_kind_{quality['page_kind']}")
                continue
            if quality["source_tier"] == "D":
                memory.record_candidate_rejection("low_value_source_tier_d")
                continue

            score = self._candidate_score(row, memory, query_usage, quality)
            if score <= -2.0:
                memory.record_candidate_rejection("off_topic_candidate")
                continue

            seen_urls.add(normalized_url)
            if title_key:
                seen_titles.add(title_key)
            domain_counts[domain] += 1
            query = memory.url_search_query.get(url, "")
            if query:
                query_usage[query] += 1
            context = f"{title}\n{row.get('snippet', '')}".strip()
            scored.append((score, url, context))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [(url, context) for _, url, context in scored[:limit]]

    @staticmethod
    def _provider_health_state(memory: WorkingMemory, provider: str) -> str:
        snapshot = (memory.search_provider_health or {}).get(provider, {})
        return str(snapshot.get("health_state") or snapshot.get("state") or "unknown")

    def _should_run_supervisor_round(
        self,
        memory: WorkingMemory,
        successful: list[ArticleCard],
        round_index: int,
        runtime: dict[str, Any],
    ) -> bool:
        if round_index >= 2:
            return False
        if len(successful) >= runtime["report_target_items"] and memory.coverage.section_count >= 2:
            return False
        if len(successful) >= runtime["max_extractions_per_run"]:
            return False
        if not memory.coverage.gaps():
            return False
        return True

    def _build_supervisor_action(
        self,
        memory: WorkingMemory,
        successful: list[ArticleCard],
        round_index: int,
    ) -> dict[str, Any]:
        gaps = memory.coverage.gaps()
        target_sections: list[str] = []
        query_strategy: list[str] = []
        joined_gaps = "；".join(gaps)
        if "政策" in joined_gaps:
            target_sections.append("policy")
            query_strategy.append("政策标准/监管更新/标准发布/EPR/CBAM")
        if "学术" in joined_gaps:
            target_sections.append("academic")
            query_strategy.append("期刊论文/研究进展/材料机理/加工突破")
        if "产业" in joined_gaps:
            target_sections.append("industry")
            query_strategy.append("设备新品/企业扩产/材料应用/工艺升级")
        provider_health = {
            provider: self._provider_health_state(memory, provider)
            for provider in ["zhipu", "brave"]
        }
        reason = "；".join(gaps) if gaps else "需要扩展更多高质量主题"
        return {
            "round": round_index,
            "reason": reason,
            "successful_articles": len(successful),
            "section_count": memory.coverage.section_count,
            "target_sections": target_sections or ["industry", "policy", "academic"],
            "query_strategy": query_strategy or ["广覆盖补洞"],
            "provider_health_snapshot": provider_health,
            "searched_queries": list(memory.searched_queries[-6:]),
        }

    def _candidate_score(
        self,
        row: dict[str, Any],
        memory: WorkingMemory,
        query_usage: dict[str, int],
        quality: dict[str, Any],
    ) -> float:
        url = row.get("url", "")
        domain = row.get("domain") or extract_domain(url)
        title = row.get("title", "")
        snippet = row.get("snippet", "")
        text = f"{title} {snippet}".lower()
        score = 0.0

        pub = row.get("published_at")
        if pub is not None:
            now_utc = now_local().astimezone(pub.tzinfo) if getattr(pub, "tzinfo", None) else now_local()
            age_days = max((now_utc.replace(tzinfo=None) - pub.replace(tzinfo=None)).days, 0)
            if age_days <= 7:
                score += 4.0
            else:
                score -= 3.0
        else:
            score -= 1.5

        score += float(SOURCE_TIER_RANK.get(quality["source_tier"], 1)) * 1.5
        positive_hits = sum(1 for keyword in _POSITIVE_KEYWORDS if keyword.lower() in text)
        negative_hits = sum(1 for keyword in _NEGATIVE_KEYWORDS if keyword.lower() in text)
        if negative_hits > 0 and positive_hits == 0 and quality["source_tier"] in {"C", "D"}:
            return -5.0
        score += positive_hits * 0.5
        score -= negative_hits * 3.0
        if negative_hits > 0 and positive_hits == 0 and SOURCE_TIER_RANK.get(quality["source_tier"], 1) <= 2:
            score -= 3.0

        query = memory.url_search_query.get(url, "")
        score += max(0.0, 1.5 - float(query_usage.get(query, 0)))
        return score

    # ── Prompt 构建 ───────────────────────────────────────────

    def _build_search_prompt(self, target_date: date) -> str:
        now = now_local()
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请搜索今日《{settings.report_title}》（{target_date.isoformat()}）需要的文章素材。\n"
            f"请确保搜索词覆盖至少 4 个不同子话题（如设备新品、原料行情、政策法规、下游应用），中英文各半，避免重复搜索同一主题的近义词。\n"
            f"时效要求：只收录过去 7 天内的内容。不要在搜索词中加年份。\n"
        )

    def _build_supplement_search_prompt(self, target_date: date, memory: WorkingMemory) -> str:
        """补充搜索轮的 prompt，针对覆盖缺口搜索。"""
        gaps = memory.coverage.gaps()
        gap_desc = "；".join(gaps) if gaps else "需要更多不同维度的内容"
        searched = ", ".join(memory.searched_queries[-8:]) if memory.searched_queries else "无"
        provider_health = "，".join(
            f"{provider}:{self._provider_health_state(memory, provider)}"
            for provider in ["zhipu", "brave"]
            if (memory.search_provider_health or {}).get(provider)
        ) or "暂无 provider 健康信息"
        return (
            f"补充搜索轮。当前覆盖缺口：{gap_desc}\n"
            f"最近已搜索过的词：{searched}\n\n"
            f"当前搜索服务状态：{provider_health}\n"
            f"请针对缺口方向搜索新的关键词，重点补充不足的板块。\n"
            f"严禁主动在搜索词中加入 2024、2025 等往年年份，除非用户明确要求回顾；优先搜索今日/近7天动态。\n"
            f"时效要求：过去 7 天内的内容。\n"
            f"完成 4 轮以上搜索后即可 finish。\n"
        )

    def _build_synthesis_prompt(self, memory: WorkingMemory, target_date: date) -> str:
        articles = memory.publishable_articles()
        section_counts: dict[str, int] = {}
        for a in articles:
            section_counts[a.section] = section_counts.get(a.section, 0) + 1

        article_list = "\n".join(
            f"- [{a.section}] {a.title} ({a.domain})" for a in articles[:12]
        )
        compiled_summary = "\n".join(
            f"- {section}: {', '.join(topic['title'] for topic in memory.get_compiled_topics(section)) or '无高可信主题'}"
            for section in ["industry", "policy", "academic"]
        )

        return (
            f"以下是今日已收集并评估的文章素材：\n\n"
            f"{article_list}\n\n"
            f"板块分布：{', '.join(f'{s} {c}篇' for s, c in section_counts.items())}\n"
            f"配图状态：已验证 {memory.coverage.verified_image_count} 张\n\n"
            f"规则层批准的正式主题：\n{compiled_summary}\n\n"
            f"请基于这些素材撰写《{settings.report_title}》（{target_date.isoformat()}）。\n"
            f"先调用 compare_sources 做辅助去重和主题说明，再只对规则层批准的主题使用 write_section 撰写各板块，最后 finish。\n"
            f"不要生成行业趋势综述，除非明确有两个以上高可信主题可互相支撑。\n"
        )

    def _build_task_prompt(self, target_date: date) -> str:
        now = now_local()
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请生成今日《{settings.report_title}》（{target_date.isoformat()}）。\n"
            f"时效要求：只收录过去 72 小时内发布的内容。不要在搜索词中加上往年年份。\n"
        )

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
        if article_count <= 0:
            return "failed", "no_articles"
        if effective_topic_count >= runtime["report_target_items"] and section_count >= 2 and (recent_verified_count >= 2 or a_tier_count >= 2):
            return "complete_auto_publish", "meets_auto_publish_gate"
        if effective_topic_count >= runtime["report_min_formal_topics"] and section_count >= 2:
            if recent_verified_count >= 1 or a_tier_count >= 1:
                return "partial_auto_publish", "meets_partial_publish_gate"
            return "hold_for_missing_quality", "insufficient_recent_verified_or_a_tier"
        return "hold_for_missing_quality", "insufficient_formal_topics_or_sections"

    @staticmethod
    def _publish_grade_from_status(status: str) -> str:
        return {
            "complete_auto_publish": "complete",
            "partial_auto_publish": "partial",
            "hold_for_missing_quality": "degraded",
        }.get(status, status)

    # ── 结果持久化 ────────────────────────────────────────────

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
        """将 Agent 结果持久化到数据库。使用独立短生命周期 session。"""
        from app.database import session_scope as _session_scope

        coverage = result.memory_snapshot.get("coverage", {}) if isinstance(result.memory_snapshot, dict) else {}
        compiled_topics_snapshot = result.memory_snapshot.get("compiled_topics", {}) if isinstance(result.memory_snapshot, dict) else {}
        compiled_topic_list = [topic for topics in compiled_topics_snapshot.values() for topic in (topics or [])]
        selected_topic_count = len(compiled_topic_list)
        selected_formal_topic_count = sum(1 for topic in compiled_topic_list if topic.get("topic_confidence") == "formal")
        provisional_topic_count = sum(1 for topic in compiled_topic_list if topic.get("topic_confidence") == "provisional")
        formal_topic_count = int(coverage.get("formal_topic_count", 0) or 0)
        section_count = int(coverage.get("section_count", 0) or 0)
        effective_topic_count = formal_topic_count or int(coverage.get("total_articles", len(result.articles)) or len(result.articles))
        recent_verified_count = sum(1 for article in result.articles if article.get("recency_status") == "recent_verified")
        a_tier_count = sum(1 for article in result.articles if article.get("source_tier") == "A")
        status, publish_gate_reason = self._auto_publish_status(
            effective_topic_count=effective_topic_count,
            section_count=section_count,
            recent_verified_count=recent_verified_count,
            a_tier_count=a_tier_count,
            article_count=len(result.articles),
            runtime=runtime,
        )
        if result.finished_reason in ("timeout", "budget_exhausted", "error") and not result.articles:
            status = "failed"
            publish_gate_reason = "pipeline_failed_without_articles"
        elif not result.articles:
            status = "failed"
            publish_gate_reason = "pipeline_produced_no_articles"
        publish_grade = self._publish_grade_from_status(status)

        if result.sections_content:
            markdown_content = "\n\n".join(result.sections_content.values())
        else:
            markdown_content = "报告生成失败/内容不足。"

        # 前置"编者按"洞察摘要
        if result.editorial:
            editorial_block = f"> **编者按**：{result.editorial}"
            markdown_content = editorial_block + "\n\n---\n\n" + markdown_content
        title = result.title or f"高分子材料加工每日资讯 ({target_date.strftime('%Y-%m-%d')})"

        with _session_scope() as session:
            run = session.get(RetrievalRun, run_id)
            agent_run = session.get(AgentRun, agent_run_id)
            llm_metrics = llm_client.snapshot_metrics()
            synthesis_metrics = synthesis_llm_client.snapshot_metrics()

            report = Report(
                report_date=target_date,
                status=status,
                title=title,
                markdown_content=markdown_content,
                summary=result.summary or "无摘要",
                pipeline_version="agent-v2",
                retrieval_run_id=run_id,
                error_message=result.finished_reason if status == "failed" else None,
            )
            session.add(report)
            session.flush()

            for idx, article in enumerate(result.articles):
                try:
                    pub_attr = article.get("published_at")
                    if pub_attr is None:
                        pub_dt = now_local()
                    elif isinstance(pub_attr, str):
                        pub_dt = datetime.strptime(pub_attr[:10], "%Y-%m-%d")
                    else:
                        pub_dt = pub_attr
                except Exception:
                    pub_dt = now_local()

                item = ReportItem(
                    report_id=report.id,
                    article_id=None,
                    section=article.get("section", "industry"),
                    rank=idx + 1,
                    title=article.get("title", ""),
                    source_name=article.get("source_name", "") or article.get("domain", "") or "agent",
                    source_url=article.get("resolved_url") or article.get("url", ""),
                    published_at=pub_dt,
                    summary=article.get("summary", "") or "由 AI 总结",
                    research_signal=article.get("key_finding", "") or "基于 Agent 生成",
                    image_url=article.get("image_url", ""),
                    has_verified_image=bool(article.get("image_url")),
                    combined_score=float(article.get("relevance_score", 0.6) or 0.6),
                    decision_trace={
                        "search_query": article.get("search_query", ""),
                        "evaluation_reason": article.get("evaluation_reason", ""),
                        "key_finding": article.get("key_finding", ""),
                        "source_domain": article.get("domain", ""),
                        "section": article.get("section", ""),
                        "source_tier": article.get("source_tier", ""),
                        "source_reliability_label": article.get("source_reliability_label", ""),
                        "source_kind": article.get("source_kind", ""),
                        "page_kind": article.get("page_kind", ""),
                        "evidence_strength": article.get("evidence_strength", ""),
                        "supports_numeric_claims": bool(article.get("supports_numeric_claims", False)),
                        "allowed_for_trend_summary": bool(article.get("allowed_for_trend_summary", False)),
                        "selection_reason": article.get("selection_reason", ""),
                        "topic_confidence": article.get("topic_confidence", ""),
                        "recency_status": article.get("recency_status", "unknown"),
                        "published_at_source": article.get("published_at_source", ""),
                    },
                )
                session.add(item)

            if run:
                run.status = status
                run.finished_at = now_local()
                run.extracted_count = len(result.articles)
                run.debug_payload = {
                    "agent_finished_reason": result.finished_reason,
                    "agent_steps": result.step_count,
                    "agent_articles": len(result.articles),
                    "selected_count": len(result.articles),
                    "section_coverage": section_count,
                    "image_selected_count": sum(1 for article in result.articles if article.get("image_url")),
                    "publishable_count": len(result.articles),
                    "publish_grade": publish_grade,
                    "publish_gate_reason": publish_gate_reason,
                    "formal_topic_count": selected_formal_topic_count,
                    "provisional_topic_count": provisional_topic_count,
                    "selected_topic_count": selected_topic_count,
                    "recent_verified_count": recent_verified_count,
                    "a_tier_count": a_tier_count,
                    "harness_status": result.harness_status,
                    "runtime": runtime,
                    "model_fallbacks": llm_metrics.get("model_fallbacks", []),
                    "llm_bad_request_count": llm_metrics.get("llm_bad_request_count", 0),
                    "llm_no_tool_stall_count": int(result.diagnostics.get("llm_no_tool_stall_count", 0)),
                    "scrape_layer_stats": result.memory_snapshot.get("scrape_layer_stats", {}),
                    "domain_failures": result.memory_snapshot.get("domain_failures", {}),
                    "candidate_rejection_reasons": result.memory_snapshot.get("candidate_rejection_reasons", {}),
                    "search_provider_health": result.memory_snapshot.get("search_provider_health", {}),
                    "tool_use_model": llm_metrics.get("tool_use_model", llm_client.primary_model),
                    "tool_use_model_switch_attempted": llm_metrics.get("tool_use_model_switch_attempted", False),
                    "tool_use_history_reset_count": llm_metrics.get("tool_use_history_reset_count", 0),
                    "moonshot_reasoning_history_errors": llm_metrics.get("moonshot_reasoning_history_errors", 0),
                    "kimi_rate_limit_errors": llm_metrics.get("kimi_rate_limit_errors", 0),
                    "strict_primary_model_enabled": llm_metrics.get("strict_primary_model_enabled", True),
                    "tool_use_fallback_mode": llm_metrics.get("tool_use_fallback_mode", "disabled"),
                    "synthesis_model_used": synthesis_metrics.get("tool_use_model", synthesis_llm_client.primary_model),
                    "synthesis_fallback_triggered": bool(synthesis_metrics.get("model_fallbacks", [])),
                    "phase3_compare_status": result.diagnostics.get("phase3_compare_status", {}),
                    "phase3_section_results": result.diagnostics.get("phase3_section_results", {}),
                    "phase3_total_duration_seconds": result.diagnostics.get("phase3_total_duration_seconds", 0),
                    "supplement_candidates_found": result.diagnostics.get("supplement_candidates_found", 0),
                    "supplement_agents_launched": result.diagnostics.get("supplement_agents_launched", 0),
                    "supplement_successful_articles": result.diagnostics.get("supplement_successful_articles", 0),
                    "phase2_rejected_missing_date_count": result.diagnostics.get("phase2_rejected_missing_date_count", 0),
                    "phase2_rejected_stale_count": result.diagnostics.get("phase2_rejected_stale_count", 0),
                    "phase2_soft_accepted_unknown_date_count": result.diagnostics.get("phase2_soft_accepted_unknown_date_count", 0),
                    "phase2_attempted_articles": result.diagnostics.get("phase2_attempted_articles", 0),
                    "phase2_successful_articles": result.diagnostics.get("phase2_successful_articles", 0),
                    "supervisor_actions": result.diagnostics.get("supervisor_actions", []),
                    "section_write_timeouts": result.memory_snapshot.get("section_write_timeouts", []),
                    "section_generation_mode": result.memory_snapshot.get("section_generation_mode", {}),
                    "pipeline_version": "agent-v2",
                }

            if agent_run:
                agent_run.status = status
                agent_run.finished_reason = result.finished_reason
                agent_run.total_steps = result.step_count
                agent_run.total_tokens = result.total_tokens
                agent_run.memory_snapshot = result.memory_snapshot
                agent_run.debug_payload = {
                    "diagnostics": result.diagnostics,
                    "model_fallbacks": llm_metrics.get("model_fallbacks", []),
                    "llm_bad_request_count": llm_metrics.get("llm_bad_request_count", 0),
                    "scrape_layer_stats": result.memory_snapshot.get("scrape_layer_stats", {}),
                    "domain_failures": result.memory_snapshot.get("domain_failures", {}),
                    "candidate_rejection_reasons": result.memory_snapshot.get("candidate_rejection_reasons", {}),
                    "search_provider_health": result.memory_snapshot.get("search_provider_health", {}),
                    "tool_use_model": llm_metrics.get("tool_use_model", llm_client.primary_model),
                    "tool_use_model_switch_attempted": llm_metrics.get("tool_use_model_switch_attempted", False),
                    "tool_use_history_reset_count": llm_metrics.get("tool_use_history_reset_count", 0),
                    "moonshot_reasoning_history_errors": llm_metrics.get("moonshot_reasoning_history_errors", 0),
                    "kimi_rate_limit_errors": llm_metrics.get("kimi_rate_limit_errors", 0),
                    "strict_primary_model_enabled": llm_metrics.get("strict_primary_model_enabled", True),
                    "tool_use_fallback_mode": llm_metrics.get("tool_use_fallback_mode", "disabled"),
                    "synthesis_model_used": synthesis_metrics.get("tool_use_model", synthesis_llm_client.primary_model),
                    "synthesis_fallback_triggered": bool(synthesis_metrics.get("model_fallbacks", [])),
                    "phase3_compare_status": result.diagnostics.get("phase3_compare_status", {}),
                    "phase3_section_results": result.diagnostics.get("phase3_section_results", {}),
                    "phase3_total_duration_seconds": result.diagnostics.get("phase3_total_duration_seconds", 0),
                    "selected_count": len(result.articles),
                    "section_coverage": section_count,
                    "image_selected_count": sum(1 for article in result.articles if article.get("image_url")),
                    "publishable_count": len(result.articles),
                    "publish_grade": publish_grade,
                    "publish_gate_reason": publish_gate_reason,
                    "formal_topic_count": selected_formal_topic_count,
                    "provisional_topic_count": provisional_topic_count,
                    "selected_topic_count": selected_topic_count,
                    "recent_verified_count": recent_verified_count,
                    "a_tier_count": a_tier_count,
                    "supplement_candidates_found": result.diagnostics.get("supplement_candidates_found", 0),
                    "supplement_agents_launched": result.diagnostics.get("supplement_agents_launched", 0),
                    "supplement_successful_articles": result.diagnostics.get("supplement_successful_articles", 0),
                    "phase2_rejected_missing_date_count": result.diagnostics.get("phase2_rejected_missing_date_count", 0),
                    "phase2_rejected_stale_count": result.diagnostics.get("phase2_rejected_stale_count", 0),
                    "phase2_soft_accepted_unknown_date_count": result.diagnostics.get("phase2_soft_accepted_unknown_date_count", 0),
                    "phase2_attempted_articles": result.diagnostics.get("phase2_attempted_articles", 0),
                    "phase2_successful_articles": result.diagnostics.get("phase2_successful_articles", 0),
                    "supervisor_actions": result.diagnostics.get("supervisor_actions", []),
                    "section_write_timeouts": result.memory_snapshot.get("section_write_timeouts", []),
                    "section_generation_mode": result.memory_snapshot.get("section_generation_mode", {}),
                }

            session.commit()
            return report
