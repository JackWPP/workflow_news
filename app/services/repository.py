from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

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
from app.utils import extract_domain, now_local


def get_latest_report_for_date(session, report_date: date) -> Report | None:
    stmt = (
        select(Report)
        .where(Report.report_date == report_date)
        .options(selectinload(Report.items))
        .order_by(desc(Report.id))
        .limit(1)
    )
    return session.scalars(stmt).first()


def get_report_by_id(session, report_id: int) -> Report | None:
    stmt = select(Report).where(Report.id == report_id).options(selectinload(Report.items))
    return session.scalars(stmt).first()


def list_reports(session, limit: int = 30) -> list[Report]:
    stmt = select(Report).options(selectinload(Report.items)).order_by(desc(Report.report_date), desc(Report.id)).limit(limit)
    return list(session.scalars(stmt).all())


def list_history_dates(session) -> list[date]:
    stmt = select(Report.report_date).distinct().order_by(desc(Report.report_date))
    return list(session.scalars(stmt).all())


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
    recent_runs = session.scalars(select(RetrievalRun).where(RetrievalRun.run_date >= since).order_by(desc(RetrievalRun.id))).all()
    for run in recent_runs:
        payload = run.debug_payload or {}
        for reason, count in (payload.get("rejection_counts") or {}).items():
            if reason in {"blocked_domain", "off_topic_candidate", "off_topic_content", "pr_like_candidate", "pr_like_content"}:
                hard_rejects[reason] += int(count)
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
    }
