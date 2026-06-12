from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.models import AgentRun, Report, ReportItem, RetrievalRun
from app.services.llm_client import LLMClient
from app.utils import extract_domain, now_local

logger = logging.getLogger(__name__)

_SOURCE_DISPLAY_NAME_MAP = {
    "finance.sina.com.cn": "新浪财经",
    "k.sina.com.cn": "新浪看点",
    "sinopecnews.com.cn": "中国石化新闻网",
    "paper.sciencenet.cn": "科学网",
    "news.mit.edu": "MIT News",
    "nature.com": "Nature",
    "mdpi.com": "MDPI",
    "plasticsnews.com": "Plastics News",
    "ptonline.com": "Plastics Technology",
    "plasticstoday.com": "PlasticsToday",
    "kingfa.com.cn": "金发科技",
    "miit.gov.cn": "工信部",
}


def auto_publish_status(
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
    if (
        effective_topic_count >= runtime["report_target_items"]
        and section_count >= 2
        and (recent_verified_count >= 2 or a_tier_count >= 2)
    ):
        return "complete_auto_publish", "meets_auto_publish_gate"
    if (
        effective_topic_count >= runtime["report_min_formal_topics"]
        and section_count >= 2
    ):
        if recent_verified_count >= 1 or a_tier_count >= 1:
            return "partial_auto_publish", "meets_partial_publish_gate"
        return "hold_for_missing_quality", "insufficient_recent_verified_or_a_tier"
    return "hold_for_missing_quality", "insufficient_formal_topics_or_sections"


def publish_grade_from_status(status: str) -> str:
    return {
        "complete_auto_publish": "complete",
        "partial_auto_publish": "partial",
        "hold_for_missing_quality": "degraded",
    }.get(status, status)


def _infer_language(domain: str) -> str:
    domain_lower = domain.lower()
    if domain_lower.endswith(".cn") or ".com.cn" in domain_lower:
        return "zh"
    for tld in (".tw", ".hk", ".jp", ".kr"):
        if domain_lower.endswith(tld) or f"{tld}/" in domain_lower:
            return "zh"
    return "zh" if any(kw in domain_lower for kw in ["sina", "sohu", "qq", "163", "36kr"]) else "en"


def _display_source_name(article: dict[str, Any]) -> str:
    raw_name = str(article.get("source_name") or "").strip()
    domain = extract_domain(
        str(article.get("resolved_url") or article.get("url") or "")
    )
    if domain in _SOURCE_DISPLAY_NAME_MAP:
        return _SOURCE_DISPLAY_NAME_MAP[domain]
    if raw_name and "." not in raw_name:
        return raw_name
    if raw_name in _SOURCE_DISPLAY_NAME_MAP:
        return _SOURCE_DISPLAY_NAME_MAP[raw_name]
    candidate = raw_name or domain or "agent"
    candidate = candidate.replace("www.", "")
    return candidate


async def result_to_report(
    result: Any,
    target_date: date,
    run_id: int,
    agent_run_id: int,
    shadow_mode: bool | None,
    mode: str,
    runtime: dict[str, Any],
    llm_client: LLMClient,
    synthesis_llm_client: LLMClient,
) -> Report:
    from app.database import session_scope as _session_scope
    from app.services.agent_core import AgentResult

    coverage = (
        result.memory_snapshot.get("coverage", {})
        if isinstance(result.memory_snapshot, dict)
        else {}
    )
    compiled_topics_snapshot = (
        result.memory_snapshot.get("compiled_topics", {})
        if isinstance(result.memory_snapshot, dict)
        else {}
    )
    compiled_topic_list = [
        topic
        for topics in compiled_topics_snapshot.values()
        for topic in (topics or [])
    ]
    selected_topic_count = len(compiled_topic_list)
    selected_formal_topic_count = sum(
        1
        for topic in compiled_topic_list
        if topic.get("topic_confidence") == "formal"
    )
    provisional_topic_count = sum(
        1
        for topic in compiled_topic_list
        if topic.get("topic_confidence") == "provisional"
    )
    formal_topic_count = int(coverage.get("formal_topic_count", 0) or 0)
    section_count = int(coverage.get("section_count", 0) or 0)
    effective_topic_count = formal_topic_count or int(
        coverage.get("total_articles", len(result.articles)) or len(result.articles)
    )
    recent_verified_count = sum(
        1
        for article in result.articles
        if article.get("recency_status") == "recent_verified"
    )
    a_tier_count = sum(
        1 for article in result.articles if article.get("source_tier") == "A"
    )
    status, publish_gate_reason = auto_publish_status(
        effective_topic_count=effective_topic_count,
        section_count=section_count,
        recent_verified_count=recent_verified_count,
        a_tier_count=a_tier_count,
        article_count=len(result.articles),
        runtime=runtime,
    )
    if (
        result.finished_reason in ("timeout", "budget_exhausted", "error")
        and not result.articles
    ):
        status = "failed"
        publish_gate_reason = "pipeline_failed_without_articles"
    elif not result.articles:
        status = "failed"
        publish_gate_reason = "pipeline_produced_no_articles"
    publish_grade = publish_grade_from_status(status)

    if result.sections_content:
        _CANONICAL_ORDER = ["industry", "policy", "academic", "patent", "wechat", "lab_news"]
        ordered_sections: dict[str, str] = {}
        for key in _CANONICAL_ORDER:
            if key in result.sections_content:
                ordered_sections[key] = result.sections_content[key]
        for key in result.sections_content:
            if key not in ordered_sections:
                ordered_sections[key] = result.sections_content[key]
        markdown_content = "\n\n".join(ordered_sections.values())
    else:
        markdown_content = "报告生成失败/内容不足。"

    if result.editorial:
        editorial_block = f"> **编者按**：{result.editorial}"
        markdown_content = editorial_block + "\n\n---\n\n" + markdown_content

    if result.daily_briefing:
        briefing_block = f"## 每日洞察\n\n{result.daily_briefing}"
        markdown_content = markdown_content + "\n\n---\n\n" + briefing_block

    final_summary = result.summary
    if result.daily_briefing:
        final_summary = result.daily_briefing[:120]

    title = (
        result.title
        or f"高分子材料加工每日资讯 ({target_date.strftime('%Y-%m-%d')})"
    )

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
            summary=final_summary or result.summary or "无摘要",
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
                source_name=_display_source_name(article),
                source_url=article.get("resolved_url") or article.get("url", ""),
                published_at=pub_dt,
                summary=article.get("summary", "") or "由 AI 总结",
                research_signal=article.get("key_finding", "") or "基于 Agent 生成",
                image_url=article.get("image_url", ""),
                has_verified_image=bool(article.get("image_url")),
                combined_score=float(article.get("relevance_score", 0.6) or 0.6),
                language=_infer_language(article.get("domain", "")),
                decision_trace={
                    "search_query": article.get("search_query", ""),
                    "evaluation_reason": article.get("evaluation_reason", ""),
                    "key_finding": article.get("key_finding", ""),
                    "source_domain": article.get("domain", ""),
                    "section": article.get("section", ""),
                    "source_tier": article.get("source_tier", ""),
                    "source_reliability_label": article.get(
                        "source_reliability_label", ""
                    ),
                    "source_kind": article.get("source_kind", ""),
                    "page_kind": article.get("page_kind", ""),
                    "category": article.get("category", "高材制造"),
                    "evidence_strength": article.get("evidence_strength", ""),
                    "supports_numeric_claims": bool(
                        article.get("supports_numeric_claims", False)
                    ),
                    "allowed_for_trend_summary": bool(
                        article.get("allowed_for_trend_summary", False)
                    ),
                    "selection_reason": article.get("selection_reason", ""),
                    "topic_confidence": article.get("topic_confidence", ""),
                    "recency_status": article.get("recency_status", "unknown"),
                    "published_at_source": article.get("published_at_source", ""),
                    "language": article.get("language", _infer_language(article.get("domain", ""))),
                    "keywords": article.get("keywords", []),
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
                "image_selected_count": sum(
                    1 for article in result.articles if article.get("image_url")
                ),
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
                "llm_bad_request_count": llm_metrics.get(
                    "llm_bad_request_count", 0
                ),
                "llm_no_tool_stall_count": int(
                    result.diagnostics.get("llm_no_tool_stall_count", 0)
                ),
                "scrape_layer_stats": result.memory_snapshot.get(
                    "scrape_layer_stats", {}
                ),
                "domain_failures": result.memory_snapshot.get(
                    "domain_failures", {}
                ),
                "candidate_rejection_reasons": result.memory_snapshot.get(
                    "candidate_rejection_reasons", {}
                ),
                "search_provider_health": result.memory_snapshot.get(
                    "search_provider_health", {}
                ),
                "tool_use_model": llm_metrics.get(
                    "tool_use_model", llm_client.primary_model
                ),
                "tool_use_model_switch_attempted": llm_metrics.get(
                    "tool_use_model_switch_attempted", False
                ),
                "tool_use_history_reset_count": llm_metrics.get(
                    "tool_use_history_reset_count", 0
                ),
                "moonshot_reasoning_history_errors": llm_metrics.get(
                    "moonshot_reasoning_history_errors", 0
                ),
                "kimi_rate_limit_errors": llm_metrics.get(
                    "kimi_rate_limit_errors", 0
                ),
                "strict_primary_model_enabled": llm_metrics.get(
                    "strict_primary_model_enabled", True
                ),
                "tool_use_fallback_mode": llm_metrics.get(
                    "tool_use_fallback_mode", "disabled"
                ),
                "synthesis_model_used": synthesis_metrics.get(
                    "tool_use_model", synthesis_llm_client.primary_model
                ),
                "synthesis_fallback_triggered": bool(
                    synthesis_metrics.get("model_fallbacks", [])
                ),
                "phase3_compare_status": result.diagnostics.get(
                    "phase3_compare_status", {}
                ),
                "phase3_section_results": result.diagnostics.get(
                    "phase3_section_results", {}
                ),
                "phase3_total_duration_seconds": result.diagnostics.get(
                    "phase3_total_duration_seconds", 0
                ),
                "phase2_rejected_missing_date_count": result.diagnostics.get(
                    "phase2_rejected_missing_date_count", 0
                ),
                "phase2_rejected_stale_count": result.diagnostics.get(
                    "phase2_rejected_stale_count", 0
                ),
                "phase2_soft_accepted_unknown_date_count": result.diagnostics.get(
                    "phase2_soft_accepted_unknown_date_count", 0
                ),
                "phase2_attempted_articles": result.diagnostics.get(
                    "phase2_attempted_articles", 0
                ),
                "phase2_successful_articles": result.diagnostics.get(
                    "phase2_successful_articles", 0
                ),
                "section_write_timeouts": result.memory_snapshot.get(
                    "section_write_timeouts", []
                ),
                "section_generation_mode": result.memory_snapshot.get(
                    "section_generation_mode", {}
                ),
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
                "llm_bad_request_count": llm_metrics.get(
                    "llm_bad_request_count", 0
                ),
                "scrape_layer_stats": result.memory_snapshot.get(
                    "scrape_layer_stats", {}
                ),
                "domain_failures": result.memory_snapshot.get(
                    "domain_failures", {}
                ),
                "candidate_rejection_reasons": result.memory_snapshot.get(
                    "candidate_rejection_reasons", {}
                ),
                "search_provider_health": result.memory_snapshot.get(
                    "search_provider_health", {}
                ),
                "tool_use_model": llm_metrics.get(
                    "tool_use_model", llm_client.primary_model
                ),
                "tool_use_model_switch_attempted": llm_metrics.get(
                    "tool_use_model_switch_attempted", False
                ),
                "tool_use_history_reset_count": llm_metrics.get(
                    "tool_use_history_reset_count", 0
                ),
                "moonshot_reasoning_history_errors": llm_metrics.get(
                    "moonshot_reasoning_history_errors", 0
                ),
                "kimi_rate_limit_errors": llm_metrics.get(
                    "kimi_rate_limit_errors", 0
                ),
                "strict_primary_model_enabled": llm_metrics.get(
                    "strict_primary_model_enabled", True
                ),
                "tool_use_fallback_mode": llm_metrics.get(
                    "tool_use_fallback_mode", "disabled"
                ),
                "synthesis_model_used": synthesis_metrics.get(
                    "tool_use_model", synthesis_llm_client.primary_model
                ),
                "synthesis_fallback_triggered": bool(
                    synthesis_metrics.get("model_fallbacks", [])
                ),
                "phase3_compare_status": result.diagnostics.get(
                    "phase3_compare_status", {}
                ),
                "phase3_section_results": result.diagnostics.get(
                    "phase3_section_results", {}
                ),
                "phase3_total_duration_seconds": result.diagnostics.get(
                    "phase3_total_duration_seconds", 0
                ),
                "selected_count": len(result.articles),
                "section_coverage": section_count,
                "image_selected_count": sum(
                    1 for article in result.articles if article.get("image_url")
                ),
                "publishable_count": len(result.articles),
                "publish_grade": publish_grade,
                "publish_gate_reason": publish_gate_reason,
                "formal_topic_count": selected_formal_topic_count,
                "provisional_topic_count": provisional_topic_count,
                "selected_topic_count": selected_topic_count,
                "recent_verified_count": recent_verified_count,
                "a_tier_count": a_tier_count,
                "phase2_rejected_missing_date_count": result.diagnostics.get(
                    "phase2_rejected_missing_date_count", 0
                ),
                "phase2_rejected_stale_count": result.diagnostics.get(
                    "phase2_rejected_stale_count", 0
                ),
                "phase2_soft_accepted_unknown_date_count": result.diagnostics.get(
                    "phase2_soft_accepted_unknown_date_count", 0
                ),
                "phase2_attempted_articles": result.diagnostics.get(
                    "phase2_attempted_articles", 0
                ),
                "phase2_successful_articles": result.diagnostics.get(
                    "phase2_successful_articles", 0
                ),
                "section_write_timeouts": result.memory_snapshot.get(
                    "section_write_timeouts", []
                ),
                "section_generation_mode": result.memory_snapshot.get(
                    "section_generation_mode", {}
                ),
            }

        session.commit()

        if report and report.status in ("complete", "complete_auto_publish"):
            try:
                from app.services.eval_runner import EvalRunner
                from app.services.llm_client import LLMClient
                runner = EvalRunner(judge_model="claude-opus-4-7", llm_client=LLMClient())
                await runner.evaluate_report(session, report)
            except Exception:
                logger.warning("Auto-evaluation skipped (non-fatal)", exc_info=True)

        return report
