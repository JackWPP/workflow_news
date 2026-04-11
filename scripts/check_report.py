import os, sys

sys.path.insert(0, ".")
os.environ["DATABASE_URL"] = "sqlite:///workflow_news_smoke_check.db"
os.environ["SHADOW_MODE"] = "false"

from sqlalchemy import create_engine, text
from app.bootstrap import init_db
from app.database import session_scope
from app.models import Report, ReportItem

init_db()
with session_scope() as s:
    reports = s.query(Report).order_by(Report.id.desc()).limit(3).all()
    for r in reports:
        print(f"=== Report id={r.id} status={r.status} ===")
        print(f"Title: {r.title}")
        print(f"Summary: {(r.summary or '')[:200]}")
        items = s.query(ReportItem).filter(ReportItem.report_id == r.id).all()
        print(f"Sections: {len(items)} items")
        for item in items:
            img = "Y" if item.image_url else "N"
            print(
                f"  - [{item.section}] {item.title[:60]}... score={item.quality_score} img={img}"
            )
        print()
