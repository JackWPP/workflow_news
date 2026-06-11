"""
editor_agent.py — 编辑型 Agent（目标 1 的核心）

职责：从 ArticlePool 选取种子 → 评估 → 对比 → 写板块 → 输出日报
没有 web_search 工具，不受任何外部 API 影响。
"""
from __future__ import annotations

import logging
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
        agent = AgentCore(tools=tools, llm_client=self._llm_client, harness=harness)
        result = await agent.run(task=task, agent_run_id=agent_run_id)

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
                    .limit(30)
                ).all())

            # 过滤掉无正文且无摘要的（相当于 permanent_fail）
            articles = [
                a for a in articles
                if (a.raw_content and len(a.raw_content.strip()) > 50)
                or (a.summary and len(a.summary.strip()) > 20)
            ]

            if len(articles) >= min_count:
                seeds = [self._article_to_seed(a) for a in articles[:20]]
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
            return [self._article_to_seed(a) for a in articles], "best_available"

        return [], "empty"

    @staticmethod
    def _article_to_seed(article: ArticlePool) -> dict[str, Any]:
        has_content = bool(article.raw_content and len(article.raw_content.strip()) > 50)
        return {
            "id": article.id,
            "url": article.url,
            "title": article.title,
            "domain": article.domain,
            "snippet": (article.summary or "")[:200],
            "fetch_status": "ok" if has_content else "empty",
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "section": article.section or "",
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
            seed_lines.append(
                f"- [{i}] (id={s['id']}) {s['title'][:60]}（{s['domain']}）{section_hint} {status}"
            )
        seed_block = "\n".join(seed_lines)

        return (
            f"当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n\n"
            f"请生成今日《{settings.report_title}》（{target_date.isoformat()}）。\n\n"
            f"种子清单（共 {len(seeds)} 条，窗口={seed_window}）：\n{seed_block}\n\n"
            f"工作流程：\n"
            f"1. 用 read_pool_article 逐条读取种子正文（用编号对应的 id）\n"
            f"2. 每读完一条，立即用 evaluate_article 评估\n"
            f"3. 正文缺失的种子，用 read_page 尝试重新抓取\n"
            f"4. 评估完成后，用 compare_sources 做对比分析\n"
            f"5. 用 write_section 撰写各板块\n"
            f"6. 用 finish 输出完整日报\n\n"
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
        dra = DailyReportAgent()
        # 复用 DailyReportAgent 的 _result_to_report 逻辑
        return await dra._result_to_report(
            result, target_date, run_id, agent_run_id,
            shadow, "publish",
            {"shadow_mode": shadow},
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
