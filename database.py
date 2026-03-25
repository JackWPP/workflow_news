from __future__ import annotations

from datetime import date

from app.bootstrap import init_db as bootstrap_init_db
from app.database import session_scope
from app.models import Report
from app.services.repository import get_latest_report_for_date, list_history_dates
from app.utils import now_local


def init_db():
    bootstrap_init_db()


def save_news(content: str, report_date: str | None = None):
    target_date = date.fromisoformat(report_date) if report_date else now_local().date()
    with session_scope() as session:
        session.add(
            Report(
                report_date=target_date,
                status="complete",
                title=f"兼容导入日报（{target_date.isoformat()}）",
                markdown_content=content,
                summary="通过兼容层写入的日报内容。",
                pipeline_version="legacy-compat",
            )
        )


def get_latest_news_by_date(report_date: str):
    target_date = date.fromisoformat(report_date)
    with session_scope() as session:
        report = get_latest_report_for_date(session, target_date)
        if report is None:
            return None
        return {
            "id": report.id,
            "date": report.report_date.isoformat(),
            "content": report.markdown_content,
            "created_at": report.created_at.isoformat(sep=" "),
            "status": report.status,
        }


def get_history_dates():
    with session_scope() as session:
        return [value.isoformat() for value in list_history_dates(session)]


def get_today_news():
    return get_latest_news_by_date(now_local().date().isoformat())
