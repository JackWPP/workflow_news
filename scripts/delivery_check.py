"""Run smoke test against a persistent DB so we can inspect the report after."""

import os, sys

sys.path.insert(0, ".")
os.environ["DATABASE_URL"] = "sqlite:///outputs/delivery_check.db"
os.environ["SHADOW_MODE"] = "false"

from sqlalchemy import create_engine
from app.bootstrap import init_db
from app.database import session_scope
from app.models import Report, ReportItem

# Ensure outputs dir exists
os.makedirs("outputs", exist_ok=True)

init_db()

from app.services.daily_report_agent import DailyReportAgent
from app.utils import now_local

import asyncio


async def main():
    print("Starting DailyReportAgent.run() against persistent DB...")
    agent = DailyReportAgent()
    result = await agent.run(mode="publish", report_date=now_local().date())
    print(f"Agent finished. Report status: {result.get('status')}")
    print(
        f"Items: {result.get('total_items', 0)}, Sections: {result.get('total_sections', 0)}"
    )

    with session_scope() as s:
        reports = s.query(Report).order_by(Report.id.desc()).limit(1).all()
        for r in reports:
            print(f"\n=== Report id={r.id} | status={r.status} ===")
            print(f"Title: {r.title}")
            print(f"Summary: {(r.summary or '')[:300]}")
            items = s.query(ReportItem).filter(ReportItem.report_id == r.id).all()
            print(f"\nTotal items: {len(items)}")
            for item in items:
                img = "Y" if item.image_url else "N"
                print(f"  [{item.section}] score={item.quality_score:.2f} img={img}")
                print(f"    {item.title[:80]}")
                print(
                    f"    {item.markdown_content[:200] if item.markdown_content else '(no content)'}..."
                )
                print()


asyncio.run(main())
