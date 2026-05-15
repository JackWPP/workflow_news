from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import selectinload

from app.models import (
    AppSetting,
    AuthSession,
    Conversation,
    FavoriteConversation,
    FavoriteReport,
    Message,
    QualityFeedback,
    Report,
    ReportItem,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalRun,
    Source,
    User,
)
from app.services.evaluation import build_evaluation_summary, enrich_debug_payload, run_offline_benchmark
from app.utils import extract_domain, now_local


def get_latest_report_for_date(
    session, report_date: date, report_type: str | None = None
) -> Report | None:
    stmt = (
        select(Report)
        .where(Report.report_date == report_date)
        .options(selectinload(Report.items))
        .order_by(desc(Report.id))
        .limit(1)
    )
    if report_type:
        stmt = stmt.where(Report.report_type == report_type)
    return session.scalars(stmt).first()


def get_report_by_id(session, report_id: int) -> Report | None:
    stmt = select(Report).where(Report.id == report_id).options(selectinload(Report.items))
    return session.scalars(stmt).first()


def list_reports(
    session, limit: int = 30, report_type: str | None = None
) -> list[Report]:
    stmt = (
        select(Report)
        .options(selectinload(Report.items))
        .order_by(desc(Report.report_date), desc(Report.id))
        .limit(limit)
    )
    if report_type:
        stmt = stmt.where(Report.report_type == report_type)
    return list(session.scalars(stmt).all())


def list_history_dates(session, report_type: str | None = None) -> list[date]:
    stmt = select(Report.report_date).distinct().order_by(desc(Report.report_date))
    if report_type:
        stmt = stmt.where(Report.report_type == report_type)
    return list(session.scalars(stmt).all())


def list_reports_for_date(session, report_date: date) -> list[Report]:
    stmt = (
        select(Report)
        .where(Report.report_date == report_date)
        .options(selectinload(Report.items))
        .order_by(desc(Report.id))
    )
    return list(session.scalars(stmt).all())


def _coerce_iso_datetime(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _clone_report_item(item: Any, category_override: str | None = None) -> SimpleNamespace:
    decision_trace = dict(getattr(item, "decision_trace", {}) or {})
    if category_override:
        decision_trace["category"] = category_override
    image_url = getattr(item, "image_url", None)
    has_verified_image = bool(getattr(item, "has_verified_image", False))
    return SimpleNamespace(
        id=getattr(item, "id"),
        section=getattr(item, "section"),
        rank=getattr(item, "rank", 0),
        title=getattr(item, "title"),
        source_name=getattr(item, "source_name"),
        source_url=getattr(item, "source_url"),
        published_at=getattr(item, "published_at", None),
        summary=getattr(item, "summary"),
        research_signal=getattr(item, "research_signal"),
        image_url=image_url,
        image_source_url=getattr(item, "image_source_url", None),
        image_origin_type=getattr(item, "image_origin_type", None),
        image_caption=getattr(item, "image_caption", None),
        image_relevance_score=float(getattr(item, "image_relevance_score", 0.0) or 0.0),
        has_verified_image=has_verified_image,
        visual_verdict=getattr(item, "visual_verdict", None),
        context_verdict=getattr(item, "context_verdict", None),
        visual_score=1.0 if has_verified_image else 0.0,
        context_score=1.0 if has_verified_image else 0.0,
        final_image_score=float(getattr(item, "image_relevance_score", 0.0) or 0.0),
        selected_for_publish=bool(getattr(item, "selected_for_publish", False)),
        image_reason=getattr(item, "image_reason", None),
        window_bucket=getattr(item, "window_bucket", "primary_24h"),
        citations=list(getattr(item, "citations", []) or []),
        combined_score=float(getattr(item, "combined_score", 0.0) or 0.0),
        decision_trace=decision_trace,
        language=getattr(item, "language", "zh"),
    )


def build_combined_report_payload(reports: list[Report]) -> SimpleNamespace | None:
    if not reports:
        return None

    global_report = next((report for report in reports if report.report_type == "global"), None)
    ai_report = next((report for report in reports if report.report_type == "ai"), None)
    lab_report = next((report for report in reports if report.report_type == "lab"), None)
    primary = global_report or ai_report or lab_report or reports[0]
    items: list[SimpleNamespace] = []

    if global_report:
        items.extend(_clone_report_item(item) for item in global_report.items)
    if ai_report:
        items.extend(_clone_report_item(item, category_override="AI") for item in ai_report.items)
    if lab_report:
        items.extend(_clone_report_item(item, category_override="实验室") for item in lab_report.items)

    items.sort(
        key=lambda item: (
            item.decision_trace.get("category") not in ("AI", "实验室"),
            item.section,
            -float(item.combined_score or 0.0),
            item.rank,
            item.id,
        )
    )
    for index, item in enumerate(items, start=1):
        item.rank = index

    summary_parts: list[str] = []
    if global_report and global_report.summary:
        summary_parts.append(str(global_report.summary))
    if ai_report:
        ai_count = len(ai_report.items)
        ai_summary = str(ai_report.summary or "").strip()
        summary_parts.append(ai_summary or f"AI 日报同步 {ai_count} 条 RSS 条目。")
    if lab_report:
        lab_count = len(lab_report.items)
        lab_summary = str(lab_report.summary or "").strip()
        summary_parts.append(lab_summary or f"实验室日报 {lab_count} 条。")

    hero_item = next((item for item in items if item.has_verified_image), items[0] if items else None)
    image_count = sum(1 for item in items if item.has_verified_image)

    return SimpleNamespace(
        id=getattr(primary, "id"),
        report_date=getattr(primary, "report_date"),
        status=getattr(primary, "status"),
        title=getattr(primary, "title"),
        markdown_content="\n\n".join(
            str(content).strip()
            for content in [
                getattr(global_report, "markdown_content", None),
                getattr(ai_report, "markdown_content", None),
                getattr(lab_report, "markdown_content", None),
            ]
            if content
        ),
        summary=" ".join(summary_parts).strip() or getattr(primary, "summary", None),
        pipeline_version="combined-v1",
        debug_url=getattr(primary, "debug_url", None),
        error_message=getattr(primary, "error_message", None),
        publish_grade=getattr(primary, "publish_grade", getattr(primary, "status", "partial")),
        round_count=getattr(primary, "round_count", 1),
        supervisor_actions=getattr(primary, "supervisor_actions", []),
        hero_image={"url": hero_item.image_url} if hero_item and hero_item.image_url else None,
        image_review_summary={"verified_image_count": image_count},
        created_at=getattr(primary, "created_at"),
        report_type="combined",
        categories=["高材制造", "清洁能源", "AI"],
        english_section_count=sum(1 for item in items if item.language == "en"),
        chinese_section_count=sum(1 for item in items if item.language != "en"),
        overall_score=None,
        items=items,
    )


def get_combined_report_for_date(session, report_date: date) -> SimpleNamespace | None:
    reports = list_reports_for_date(session, report_date)
    return build_combined_report_payload(reports)


def list_combined_reports(session, limit: int = 30) -> list[SimpleNamespace]:
    dates = list_history_dates(session)
    combined: list[SimpleNamespace] = []
    for report_date in dates:
        payload = get_combined_report_for_date(session, report_date)
        if payload is not None:
            combined.append(payload)
        if len(combined) >= limit:
            break
    return combined[:limit]


def list_sources(session) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.priority.desc(), Source.domain.asc())).all())


def replace_sources(session, payloads: list[dict]) -> list[Source]:
    session.execute(delete(Source))
    for payload in payloads:
        session.add(Source(**payload))
    session.flush()
    return list_sources(session)


def get_report_settings(session) -> dict:
    setting = session.get(AppSetting, "report_settings")
    return setting.value if setting else {}


def update_report_settings(session, payload: dict) -> dict:
    setting = session.get(AppSetting, "report_settings")
    if setting is None:
        setting = AppSetting(key="report_settings", value=payload)
        session.add(setting)
    else:
        setting.value = payload
    session.flush()
    return setting.value


def list_retrieval_runs(session) -> list[RetrievalRun]:
    stmt = select(RetrievalRun).order_by(desc(RetrievalRun.id))
    return list(session.scalars(stmt).all())


def list_retrieval_queries(session, run_id: int) -> list[RetrievalQuery]:
    stmt = select(RetrievalQuery).where(RetrievalQuery.run_id == run_id).order_by(RetrievalQuery.id.asc())
    return list(session.scalars(stmt).all())


def list_retrieval_candidates(session, run_id: int) -> list[RetrievalCandidate]:
    stmt = select(RetrievalCandidate).where(RetrievalCandidate.run_id == run_id).order_by(RetrievalCandidate.id.asc())
    return list(session.scalars(stmt).all())


def get_user_by_email(session, email: str) -> User | None:
    stmt = select(User).where(User.email == email)
    return session.scalars(stmt).first()


def get_session_by_token(session, token: str) -> AuthSession | None:
    stmt = select(AuthSession).where(AuthSession.token == token)
    return session.scalars(stmt).first()


def list_conversations(session, user_id: int) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.user_id == user_id).options(selectinload(Conversation.messages)).order_by(desc(Conversation.last_message_at))
    return list(session.scalars(stmt).all())


def get_conversation(session, conversation_id: int, user_id: int) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .options(selectinload(Conversation.messages))
    )
    return session.scalars(stmt).first()


def favorite_report_ids(session, user_id: int) -> list[int]:
    stmt = select(FavoriteReport.report_id).where(FavoriteReport.user_id == user_id)
    return list(session.scalars(stmt).all())


def favorite_conversation_ids(session, user_id: int) -> list[int]:
    stmt = select(FavoriteConversation.conversation_id).where(FavoriteConversation.user_id == user_id)
    return list(session.scalars(stmt).all())


def create_quality_feedback(
    session,
    user_id: int,
    target_type: str,
    target_id: int,
    label: str,
    reason: str | None = None,
    note: str | None = None,
) -> QualityFeedback:
    if target_type == "candidate":
        target = session.get(RetrievalCandidate, target_id)
        if target is None:
            raise ValueError("Candidate not found")
        domain = target.domain
        title = target.title
    elif target_type == "report_item":
        target = session.get(ReportItem, target_id)
        if target is None:
            raise ValueError("Report item not found")
        domain = extract_domain(target.source_url) if target.source_url.startswith("http") else target.source_name
        title = target.title
    else:
        raise ValueError("Unsupported feedback target type")

    feedback = QualityFeedback(
        target_type=target_type,
        target_id=target_id,
        target_domain=domain,
        target_title=title,
        label=label,
        reason=reason,
        note=note,
        created_by=user_id,
    )
    session.add(feedback)
    session.flush()
    return feedback


def list_quality_feedback(session, limit: int = 50) -> list[QualityFeedback]:
    stmt = select(QualityFeedback).order_by(desc(QualityFeedback.id)).limit(limit)
    return list(session.scalars(stmt).all())


def quality_feedback_domain_stats(session) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    stmt = select(QualityFeedback.target_domain, QualityFeedback.label).where(QualityFeedback.target_domain.is_not(None))
    for domain, label in session.execute(stmt).all():
        if not domain or not label:
            continue
        stats[domain][label] += 1
    return {domain: dict(values) for domain, values in stats.items()}


def get_quality_overview(session, days: int = 7) -> dict:
    today = now_local().date()
    since = today - timedelta(days=days - 1)

    reports = list(
        session.scalars(
            select(Report)
            .where(Report.report_date >= since)
            .options(selectinload(Report.items))
            .order_by(desc(Report.report_date), desc(Report.id))
        ).all()
    )
    recent_feedback = list_quality_feedback(session, limit=20)

    hard_rejects: dict[str, int] = defaultdict(int)
    duplicate_trend: list[dict] = []
    source_rule_hotspots: dict[str, int] = defaultdict(int)
    high_tier_false_rejects: dict[str, int] = defaultdict(int)
    top_policy_misses: dict[str, int] = defaultdict(int)
    dominant_domain_runs: dict[str, int] = defaultdict(int)
    extended_window_usage: list[dict] = []
    no_image_rejections = 0
    image_selected_total = 0
    duplicate_image_hits = 0
    round2_trigger_count = 0
    round2_recovery_count = 0
    publish_grade_breakdown: dict[str, int] = defaultdict(int)
    image_review_rejections: dict[str, int] = defaultdict(int)
    policy_gap_breakdown: dict[str, int] = defaultdict(int)
    report_score_trend: list[dict] = []
    score_total = 0.0
    policy_filled_runs = 0
    image_filled_runs = 0
    off_topic_escape_count = 0
    recent_runs = session.scalars(select(RetrievalRun).where(RetrievalRun.run_date >= since).order_by(desc(RetrievalRun.id))).all()
    for run in recent_runs:
        payload = enrich_debug_payload(run.debug_payload, report_status=run.status)
        for reason, count in (payload.get("rejection_counts") or {}).items():
            if reason in {"blocked_domain", "off_topic_candidate", "off_topic_content", "pr_like_candidate", "pr_like_content"}:
                hard_rejects[reason] += int(count)
        report_score_trend.append(
            {
                "run_id": run.id,
                "date": run.run_date.date().isoformat(),
                "score": float(payload.get("daily_report_score", 0.0) or 0.0),
            }
        )
        score_total += float(payload.get("daily_report_score", 0.0) or 0.0)
        duplicate_trend.append(
            {
                "run_id": run.id,
                "date": run.run_date.date().isoformat(),
                "duplicate_ratio": payload.get("duplicate_ratio", 0),
            }
        )
        extended_window_usage.append(
            {
                "date": run.run_date.date().isoformat(),
                "extended_window_selected": int(payload.get("extended_window_selected", 0) or 0),
            }
        )
        for domain, count in (payload.get("source_rule_rejections") or {}).items():
            source_rule_hotspots[str(domain)] += int(count)
        for domain, count in (payload.get("high_tier_rejections") or {}).items():
            high_tier_false_rejects[str(domain)] += int(count)
        for reason, count in (payload.get("top_policy_misses") or {}).items():
            top_policy_misses[str(reason)] += int(count)
        per_domain_selected = payload.get("per_domain_selected") or {}
        if per_domain_selected:
            dominant_domain = max(per_domain_selected.items(), key=lambda item: int(item[1]))[0]
            dominant_domain_runs[str(dominant_domain)] += 1
        if int(payload.get("round_count", 1) or 1) >= 2:
            round2_trigger_count += 1
        if payload.get("round2_recovery"):
            round2_recovery_count += 1
        publish_grade_breakdown[str(payload.get("publish_grade") or run.status)] += 1
        if payload.get("policy_gap_reason"):
            policy_gap_breakdown[str(payload["policy_gap_reason"])] += 1
        if int(payload.get("policy_selected_count", 0) or 0) > 0:
            policy_filled_runs += 1
        if int(payload.get("image_selected_count", 0) or 0) >= 2:
            image_filled_runs += 1
        off_topic_escape_count += int(payload.get("off_topic_escape_count", 0) or 0)
        no_image_rejections += int(payload.get("items_without_image", 0) or 0)
        image_selected_total += int(payload.get("image_selected_count", 0) or 0)
        duplicate_image_hits += int((payload.get("image_rejections") or {}).get("low_signal_asset", 0) or 0)
        for reason, count in (payload.get("image_rejections") or {}).items():
            image_review_rejections[str(reason)] += int(count)

    domain_rollup: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for feedback in recent_feedback:
        if not feedback.target_domain:
            continue
        domain_rollup[feedback.target_domain][feedback.label] += 1

    flagged_domains = []
    for domain, counts in sorted(
        domain_rollup.items(),
        key=lambda item: item[1].get("bad_off_topic", 0) + item[1].get("bad_pr_like", 0) + item[1].get("bad_source", 0),
        reverse=True,
    )[:10]:
        flagged_domains.append(
            {
                "domain": domain,
                "bad_off_topic": counts.get("bad_off_topic", 0),
                "bad_pr_like": counts.get("bad_pr_like", 0),
                "bad_source": counts.get("bad_source", 0),
                "good": counts.get("good", 0),
                "keep_borderline": counts.get("keep_borderline", 0),
            }
        )

    daily_quality = []
    grouped: dict[date, Report] = {}
    for report in reports:
        grouped.setdefault(report.report_date, report)
    for day in sorted(grouped.keys(), reverse=True):
        report = grouped[day]
        daily_quality.append(
            {
                "date": day.isoformat(),
                "status": report.status,
                "selected_count": len(report.items),
                "section_coverage": len({item.section for item in report.items}),
            }
        )

    feedback_summary = dict(
        session.execute(select(QualityFeedback.label, func.count()).group_by(QualityFeedback.label)).all()
    )
    benchmark = run_offline_benchmark()
    run_count = len(list(recent_runs)) or 1

    return {
        "recent_feedback": [
            {
                "id": feedback.id,
                "target_type": feedback.target_type,
                "target_id": feedback.target_id,
                "target_domain": feedback.target_domain,
                "target_title": feedback.target_title,
                "label": feedback.label,
                "reason": feedback.reason,
                "note": feedback.note,
                "created_by": feedback.created_by,
                "created_at": feedback.created_at.isoformat(),
            }
            for feedback in recent_feedback
        ],
        "flagged_domains": flagged_domains,
        "hard_rejects": [{"reason": reason, "count": count} for reason, count in sorted(hard_rejects.items(), key=lambda item: item[1], reverse=True)],
        "daily_quality": daily_quality,
        "feedback_summary": feedback_summary,
        "duplicate_trend": duplicate_trend[:7],
        "source_rule_hotspots": [
            {"domain": domain, "count": count}
            for domain, count in sorted(source_rule_hotspots.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "high_tier_false_rejects": [
            {"domain": domain, "count": count}
            for domain, count in sorted(high_tier_false_rejects.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "top_policy_misses": [
            {"reason": reason, "count": count}
            for reason, count in sorted(top_policy_misses.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "extended_window_usage": extended_window_usage[:7],
        "dominant_domain_runs": [
            {"domain": domain, "count": count}
            for domain, count in sorted(dominant_domain_runs.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "image_coverage_rate": round(image_selected_total / max(sum(item["selected_count"] for item in daily_quality) or 1, 1), 4),
        "no_image_rejections": no_image_rejections,
        "duplicate_image_hits": duplicate_image_hits,
        "round2_trigger_rate": round(round2_trigger_count / run_count, 4),
        "publish_grade_breakdown": dict(publish_grade_breakdown),
        "image_review_rejections": dict(image_review_rejections),
        "policy_gap_breakdown": dict(policy_gap_breakdown),
        "report_score_trend": report_score_trend[:7],
        "benchmark_score_trend": benchmark["benchmark_score_trend"],
        "policy_fill_rate": round(policy_filled_runs / run_count, 4),
        "image_fill_rate": round(image_filled_runs / run_count, 4),
        "round2_recovery_rate": round(round2_recovery_count / max(round2_trigger_count, 1), 4) if round2_trigger_count else 0.0,
        "off_topic_escape_count": off_topic_escape_count,
        "average_daily_report_score": round(score_total / run_count, 2),
    }


def get_evaluation_summary(session, days: int = 7) -> dict:
    summary = build_evaluation_summary(session, days=days)
    summary["quality_overview"] = get_quality_overview(session, days=days)
    return summary
