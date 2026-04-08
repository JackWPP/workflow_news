import asyncio
import logging
from datetime import date, datetime
from typing import Any

from app.config import settings
from app.models import AgentRun, Report, ReportItem, RetrievalRun
from app.services.agent_core import AgentCore, AgentResult
from app.services.article_agent import ArticleAgent, ArticleCard, ArticleHarness
from app.services.brave import BraveSearchClient
from app.services.firecrawl import FirecrawlClient
from app.services.jina_reader import JinaReaderClient
from app.services.scraper import ScraperClient
from app.services.zhipu_search import ZhipuSearchClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
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
from app.utils import canonicalize_url, now_local

logger = logging.getLogger(__name__)

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

【工作流程】
1. 执行至少 8 轮 web_search，中英文各半，每轮覆盖不同子话题
2. 搜索词要具体且多样化——不要反复搜同一主题的近义词
3. 每 3-4 轮用 check_coverage 评估进度，补足缺口
4. 确保每个维度至少有 2 轮搜索覆盖后再调用 finish

注意：你不需要阅读任何页面，后续会有专门的 Agent 处理每篇文章。
"""

# ── Phase 3: 综合阶段 System Prompt ─────────────────────────
SYNTHESIS_PHASE_SYSTEM_PROMPT = """\
你是高分子材料加工日报的总编辑。
前序 Agent 已经完成了文章的搜索、阅读和评估，现在你需要：
1. 调用 compare_sources 对比去重
2. 调用 check_coverage 确认最终状态
3. 为每个有文章的板块调用 write_section 撰写内容
4. 最后调用 finish 输出完整日报

【报告质量要求】
- 每条新闻必须包含：标题、来源引用（超链接格式）、核心发现、行业影响分析
- 每条新闻至少 2 段：第 1 段事实陈述，第 2 段行业影响或趋势判断
- 不要简单复述原文，要有信息增量的解读
- 如果某板块只有 1 篇文章，深度展开该条目的分析

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
        session: Any,
        shadow_mode: bool | None = None,
        report_date: date | None = None,
        mode: str = "publish",
        event_queue: asyncio.Queue | None = None,
    ) -> Report:
        target_date = report_date or now_local().date()
        logger.info("[DailyReportAgent] Starting multi-agent run for: %s", target_date)

        # 1. 创建 DB 记录
        run = RetrievalRun(
            run_date=datetime.combine(target_date, datetime.min.time()),
            shadow_mode=settings.shadow_mode if shadow_mode is None else shadow_mode,
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

        # 2. 初始化共享资源
        brave = BraveSearchClient()
        jina = JinaReaderClient()
        firecrawl = FirecrawlClient()
        scraper = ScraperClient(jina_client=jina, firecrawl_client=firecrawl)
        zhipu = ZhipuSearchClient()
        memory = WorkingMemory()

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
            tools=search_tools, llm_client=self._llm_client, harness=search_harness,
            event_queue=event_queue,
        )

        search_prompt = self._build_search_prompt(target_date)
        search_result = await search_agent.run(
            task=search_prompt, session=session, agent_run_id=agent_run.id, memory=memory,
        )
        logger.info(
            "[DailyReportAgent] Phase 1 done: %d search results, %d queries, reason=%s",
            len(memory.search_results), len(memory.searched_queries), search_result.finished_reason,
        )

        # ============ Phase 2: 并发文章处理 ============
        logger.info("[DailyReportAgent] Phase 2: Parallel Article Processing")
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 2, "name": "文章处理"})
        candidate_urls = self._extract_candidate_urls(memory)

        if not candidate_urls:
            logger.warning("[DailyReportAgent] No candidate URLs found, falling back to monolithic mode")
            return await self._run_fallback(
                session, target_date, run, agent_run, brave, scraper, memory, shadow_mode, mode,
            )

        # 构建 sub-agent 工具
        article_tools = {
            "read_page": ReadPageTool(scraper_client=scraper),
            "evaluate_article": EvaluateArticleTool(llm_client=self._llm_client),
            "search_images": SearchImagesTool(brave_client=brave, scraper_client=scraper),
            "verify_image": VerifyImageTool(llm_client=self._llm_client),
        }

        # 创建并运行 Article Agents
        article_agents = [
            ArticleAgent(
                url=url, context=context, memory=memory,
                tools=article_tools, harness=ArticleHarness(),
                agent_run_id=agent_run.id, session=session,
            )
            for url, context in candidate_urls
        ]
        cards = await self._run_article_agents(article_agents, max_concurrency=5)

        successful = [c for c in cards if c.success and c.section != "rejected"]
        logger.info(
            "[DailyReportAgent] Phase 2 done: %d/%d articles processed successfully",
            len(successful), len(cards),
        )

        # ============ Phase 2.5 (A): 补充搜索 ============
        if len(successful) < 6 and memory.coverage.section_count < 3:
            logger.info(
                "[DailyReportAgent] Insufficient coverage (%d articles, %d sections), running supplement search",
                len(successful), memory.coverage.section_count,
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
                tools=search_tools, llm_client=self._llm_client, harness=supplement_harness,
            )
            await supplement_agent.run(
                task=supplement_prompt, session=session, agent_run_id=agent_run.id, memory=memory,
            )
            # 提取新候选 URL（memory.has_read 自动过滤已处理的）
            new_candidates = self._extract_candidate_urls(memory)
            if new_candidates:
                logger.info("[DailyReportAgent] Supplement search found %d new candidates", len(new_candidates))
                new_agents = [
                    ArticleAgent(
                        url=url, context=context, memory=memory,
                        tools=article_tools, harness=ArticleHarness(),
                        agent_run_id=agent_run.id, session=session,
                    )
                    for url, context in new_candidates[:8]
                ]
                new_cards = await self._run_article_agents(new_agents, max_concurrency=3)
                successful.extend([c for c in new_cards if c.success and c.section != "rejected"])
                logger.info("[DailyReportAgent] After supplement: %d total successful articles", len(successful))

        if not successful:
            logger.warning("[DailyReportAgent] All article agents failed, falling back")
            return await self._run_fallback(
                session, target_date, run, agent_run, brave, scraper, memory, shadow_mode, mode,
            )

        # ============ Phase 2.5: 链接可用性验证 ============
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 2.5, "name": "链接验证"})
        logger.info("[DailyReportAgent] Phase 2.5: Link Validation (%d articles)", len(successful))
        from app.services.link_checker import LinkChecker
        checker = LinkChecker()
        article_urls = [c.url for c in successful]
        image_urls = [c.image_url for c in successful if c.image_url]
        all_check_urls = article_urls + image_urls
        check_results = await checker.check_batch(all_check_urls)
        url_status = {r.url: r for r in check_results}

        valid_cards: list[ArticleCard] = []
        for card in successful:
            result = url_status.get(card.url)
            if result and not result.is_available:
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
            valid_cards.append(card)

        removed = len(successful) - len(valid_cards)
        if removed:
            logger.info("[DailyReportAgent] Link check removed %d articles, %d remain", removed, len(valid_cards))
        successful = valid_cards

        if not successful:
            logger.warning("[DailyReportAgent] All links failed validation, falling back")
            return await self._run_fallback(
                session, target_date, run, agent_run, brave, scraper, memory, shadow_mode, mode,
            )

        # ============ Phase 3: 编排综合 ============
        logger.info("[DailyReportAgent] Phase 3: Synthesis")
        if event_queue:
            event_queue.put_nowait({"type": "phase", "phase": 3, "name": "编排综合", "article_count": len(successful)})
        synthesis_tools: list = [
            CompareSourcesTool(llm_client=self._llm_client),
            WriteSectionTool(llm_client=self._llm_client),
            CheckCoverageTool(),
            FinishTool(),
        ]
        synthesis_harness = self._build_synthesis_harness()
        synthesis_agent = AgentCore(
            tools=synthesis_tools, llm_client=self._llm_client, harness=synthesis_harness,
            event_queue=event_queue,
        )

        synthesis_prompt = self._build_synthesis_prompt(memory, target_date)
        final_result = await synthesis_agent.run(
            task=synthesis_prompt, session=session, agent_run_id=agent_run.id, memory=memory,
        )
        logger.info(
            "[DailyReportAgent] Phase 3 done: %d articles, reason=%s",
            len(final_result.articles), final_result.finished_reason,
        )

        # 4. 持久化 Report
        return await self._result_to_report(
            session, final_result, target_date, run, agent_run, shadow_mode, mode,
        )

    # ── Fallback: 单体模式 ────────────────────────────────────

    async def _run_fallback(
        self,
        session: Any,
        target_date: date,
        run: RetrievalRun,
        agent_run: AgentRun,
        brave: BraveSearchClient,
        scraper: ScraperClient,
        memory: WorkingMemory,
        shadow_mode: bool | None,
        mode: str,
    ) -> Report:
        """所有 Article Agent 失败时，回退到单体 AgentCore 模式。"""
        logger.info("[DailyReportAgent] Running fallback monolithic agent")
        zhipu = ZhipuSearchClient()
        all_tools = build_all_tools(
            brave_client=brave, scraper_client=scraper, zhipu_client=zhipu, llm_client=self._llm_client,
        )
        harness = self._build_fallback_harness()
        agent = AgentCore(tools=all_tools, llm_client=self._llm_client, harness=harness)
        task = self._build_task_prompt(target_date)
        result = await agent.run(task=task, session=session, agent_run_id=agent_run.id, memory=memory)
        return await self._result_to_report(session, result, target_date, run, agent_run, shadow_mode, mode)

    # ── Article Agent 并发运行 ────────────────────────────────

    @staticmethod
    async def _run_article_agents(
        agents: list[ArticleAgent],
        max_concurrency: int = 5,
    ) -> list[ArticleCard]:
        """并发运行 Article Agents，带并发限制和超时保护。"""
        semaphore = asyncio.Semaphore(max_concurrency)

        async def run_one(agent: ArticleAgent) -> ArticleCard:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        agent.run(),
                        timeout=agent.harness.max_duration_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[ArticleAgent] Timeout for %s", agent.url[:60])
                    return ArticleCard(
                        url=agent.url, title="", domain="", source_name="",
                        published_at=None, summary="", section="rejected",
                        key_finding="", success=False, error="timeout",
                    )
                except Exception as exc:
                    logger.error("[ArticleAgent] Error for %s: %s", agent.url[:60], exc)
                    return ArticleCard(
                        url=agent.url, title="", domain="", source_name="",
                        published_at=None, summary="", section="rejected",
                        key_finding="", success=False, error=str(exc),
                    )

        results = await asyncio.gather(*[run_one(a) for a in agents])
        return list(results)

    # ── 候选 URL 提取 ─────────────────────────────────────────

    @staticmethod
    def _extract_candidate_urls(memory: WorkingMemory) -> list[tuple[str, str]]:
        """从 memory.search_results 中提取去重的候选 URL。"""
        seen: set[str] = set()
        candidates: list[tuple[str, str]] = []

        for r in memory.search_results:
            url = r.get("url", "")
            if not url:
                continue
            norm_url = canonicalize_url(url)
            if norm_url in seen or memory.has_read(norm_url):
                continue
            if any(ext in norm_url.lower() for ext in [".pdf", ".jpg", ".png", ".gif"]):
                continue
            seen.add(norm_url)
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            context = f"{title}\n{snippet}" if title else snippet
            candidates.append((url, context))

        # 最多取 18 个候选
        return candidates[:18]

    # ── Prompt 构建 ───────────────────────────────────────────

    def _build_search_prompt(self, target_date: date) -> str:
        now = datetime.now()
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
        return (
            f"补充搜索轮。当前覆盖缺口：{gap_desc}\n"
            f"最近已搜索过的词：{searched}\n\n"
            f"请针对缺口方向搜索新的关键词，重点补充不足的板块。\n"
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

        return (
            f"以下是今日已收集并评估的文章素材：\n\n"
            f"{article_list}\n\n"
            f"板块分布：{', '.join(f'{s} {c}篇' for s, c in section_counts.items())}\n"
            f"配图状态：已验证 {memory.coverage.verified_image_count} 张\n\n"
            f"请基于这些素材撰写《{settings.report_title}》（{target_date.isoformat()}）。\n"
            f"先调用 compare_sources 去重，再 write_section 撰写各板块，最后 finish。\n"
        )

    def _build_task_prompt(self, target_date: date) -> str:
        now = datetime.now()
        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请生成今日《{settings.report_title}》（{target_date.isoformat()}）。\n"
            f"时效要求：只收录过去 72 小时内发布的内容。不要在搜索词中加上往年年份。\n"
        )

    # ── 结果持久化 ────────────────────────────────────────────

    async def _result_to_report(
        self,
        session: Any,
        result: AgentResult,
        target_date: date,
        run: RetrievalRun,
        agent_run: AgentRun,
        shadow_mode: bool | None,
        mode: str,
    ) -> Report:
        status = "complete" if result.is_publishable else "partial"
        if result.finished_reason in ("timeout", "budget_exhausted", "error") and not result.articles:
            status = "failed"
        elif not result.articles:
            status = "failed"

        if result.sections_content:
            markdown_content = "\n\n".join(result.sections_content.values())
        else:
            markdown_content = "报告生成失败/内容不足。"
        title = result.title or f"高分子材料加工每日资讯 ({target_date.strftime('%Y-%m-%d')})"

        report = Report(
            report_date=target_date,
            status=status,
            title=title,
            markdown_content=markdown_content,
            summary=result.summary or "无摘要",
            pipeline_version="agent-v2",
            retrieval_run_id=run.id,
            error_message=result.finished_reason if status == "failed" else None,
        )
        session.add(report)
        session.flush()

        for idx, article in enumerate(result.articles):
            try:
                pub_attr = article.get("published_at")
                if pub_attr is None:
                    pub_dt = datetime.now()
                elif isinstance(pub_attr, str):
                    pub_dt = datetime.strptime(pub_attr[:10], "%Y-%m-%d")
                else:
                    pub_dt = pub_attr
            except Exception:
                pub_dt = datetime.now()

            item = ReportItem(
                report_id=report.id,
                article_id=None,
                section=article.get("section", "industry"),
                rank=idx + 1,
                title=article.get("title", ""),
                source_name=article.get("source_name", "") or article.get("domain", "") or "agent",
                source_url=article.get("url", ""),
                published_at=pub_dt,
                summary=article.get("summary", "") or "由 AI 总结",
                research_signal=article.get("key_finding", "") or "基于 Agent 生成",
                image_url=article.get("image_url", ""),
                has_verified_image=bool(article.get("image_url")),
                combined_score=float(article.get("relevance_score", 0.6) or 0.6),
            )
            session.add(item)

        run.status = status
        run.finished_at = datetime.now()
        run.extracted_count = len(result.articles)
        run.debug_payload = {
            "agent_finished_reason": result.finished_reason,
            "agent_steps": result.step_count,
            "agent_articles": len(result.articles),
            "harness_status": result.harness_status,
            "pipeline_version": "agent-v2",
        }

        agent_run.status = status
        agent_run.finished_reason = result.finished_reason
        agent_run.total_steps = result.step_count
        agent_run.total_tokens = result.total_tokens
        agent_run.memory_snapshot = result.memory_snapshot

        session.add(run)
        session.add(agent_run)

        session.commit()
        return report
