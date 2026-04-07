import logging
from datetime import date, datetime
from typing import Any

from app.config import settings
from app.models import AgentRun, Report, ReportItem, RetrievalRun
from app.services.agent_core import AgentCore, AgentResult
from app.services.brave import BraveSearchClient
from app.services.firecrawl import FirecrawlClient
from app.services.harness import Harness
from app.services.llm_client import LLMClient
from app.services.tools import build_all_tools
from app.utils import now_local

logger = logging.getLogger(__name__)

DAILY_REPORT_SYSTEM_PROMPT = (
    "你是一个高度专业的情报分析Agent（融合了“情报雷达”与“情报分析师”的角色）。\n"
    "你的任务是通过多轮深度搜索（覆盖产业动态、技术前沿、政策标准），"
    "收集、筛选、评估、并撰写出高质量的每日行业资讯报告。\n\n"
    "【工作指南】\n"
    "1. 独立决策：遇到信息不足时，主动变换搜索词、扩大搜索范围，而不是过早放弃。\n"
    "2. 深度评估：评估文章时，过滤掉重复、低质或非目标语境（如台湾媒体）的内容。\n"
    "3. 交叉验证：对重要数据或声明，尝试寻找多个独立来源验证。\n"
    "4. 洞察生成：提取文章的深层商业/技术信号，不能只是简单摘要。\n"
)

class DailyReportAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()

    def _build_harness(self) -> Harness:
        return Harness(
            max_steps=100,
            max_search_calls=40,
            max_page_reads=40,
            max_llm_calls=80,
            max_duration_seconds=1800.0,
            system_prompt=DAILY_REPORT_SYSTEM_PROMPT,
        )

    async def run(
        self,
        session: Any,
        shadow_mode: bool | None = None,
        report_date: date | None = None,
        mode: str = "publish",
    ) -> Report:
        target_date = report_date or now_local().date()
        logger.info("[DailyReportAgent] Starting agent run for date: %s", target_date)

        # 1. 创建 run 追踪记录
        run = RetrievalRun(
            run_date=datetime.combine(target_date, datetime.min.time()),
            shadow_mode=settings.shadow_mode if shadow_mode is None else shadow_mode,
        )
        session.add(run)
        session.flush()

        agent_run = AgentRun(
            retrieval_run_id=run.id,
            agent_type="daily_report",
        )
        session.add(agent_run)
        session.flush()
        session.commit()

        # 2. 初始化 Agent
        brave = BraveSearchClient()
        firecrawl = FirecrawlClient()
        tools = build_all_tools(
            brave_client=brave,
            firecrawl_client=firecrawl,
            llm_client=self._llm_client,
        )
        harness = self._build_harness()
        agent = AgentCore(tools=tools, llm_client=self._llm_client, harness=harness)

        # 3. 运行 Agent
        task_prompt = self._build_task_prompt(target_date)
        agent_result = await agent.run(task=task_prompt, session=session, agent_run_id=agent_run.id)

        # 4. 转换并持久化 Report
        return await self._result_to_report(session, agent_result, target_date, run, agent_run, shadow_mode, mode)

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

        # Agent 已经生成了 Markdown 和 summary，直接使用
        if result.sections_content:
            markdown_content = "\n\n".join(result.sections_content.values())
        else:
            markdown_content = "报告生成失败/内容不足。"
        title = result.title or f"高分子材料加工每日资讯 ({target_date.strftime('%Y-%m-%d')})"

        # Persist report
        report = Report(
            report_date=target_date,
            status=status,
            title=title,
            markdown_content=markdown_content,
            summary=result.summary or "无摘要",
            pipeline_version="agent-1.0",
            retrieval_run_id=run.id,
            error_message=result.finished_reason if status == "failed" else None,
        )
        session.add(report)
        session.flush()

        # Persist report items
        for idx, article in enumerate(result.articles):
            try:
                pub_attr = article.get("metadata", {}).get("published_at", None)
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
                source_name=article.get("domain", "") or "agent",
                source_url=article.get("url", ""),
                published_at=pub_dt,
                summary=article.get("summary", "") or "由 AI 总结",
                research_signal="基于 Agent 生成",
                image_url=article.get("image_url", ""),
                has_verified_image=bool(article.get("image_url")),
                combined_score=float(article.get("relevance_score", 0.6) or 0.6),
            )
            session.add(item)
        
        # update run status
        run.status = status
        run.finished_at = datetime.now()
        run.extracted_count = len(result.articles)
        run.debug_payload = {
            "agent_finished_reason": result.finished_reason,
            "agent_steps": result.step_count,
            "agent_articles": len(result.articles),
            "harness_status": result.harness_status,
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

    def _build_task_prompt(self, target_date: date) -> str:
        now = datetime.now()
        year = now.year
        month = now.month
        return (
            f"⚠️ 当前时间：{now.isoformat(' ', 'seconds')}（{settings.app_timezone}）\n"
            f"⚠️ 当前年份：{year}年{month}月\n\n"
            f"请生成今日《{settings.report_title}》。\n"
            f"**严格时效要求**：只收录过去 72 小时内发布的内容。系统会自动过滤旧闻。\n"
            f"不要在搜索词中加上往年年份——你搜索的应该是当前最新内容。\n\n"
            "⚠️ 核心要求：\n"
            "1. 必须执行至少 6 轮不同维度的 web_search（涵盖 产业、技术、政策，中英文组合）\n"
            "2. 优先级：中国大陆权威媒体、英文国际媒体\n"
            "3. 发现有价值文章后，必须用 search_images 或通过 read_page 找到真实配图\n"
            "4. 必须收集并验证至少 4 篇有价值文章（目标 6-8 篇），分成至少 2 个板块\n"
            '5. 每篇加入"💡 科研雷达"洞察分析\n'
            "6. 搜索词请用如'注塑机 最新进展'、'高分子 回收 技术'等，不要带年份\n"
            "由于你的思考过程将影响最终输出结果，请随时调用 check_coverage 以确保达到标准。所有资源准备好后再调用 finish 工具输出。\n"
        )
