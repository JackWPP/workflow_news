from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


TEST_DB = Path(tempfile.gettempdir()) / "workflow_news_ai_rss_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SHADOW_MODE"] = "false"
os.environ["OPENROUTER_API_KEY"] = ""

from fastapi.testclient import TestClient

from app.bootstrap import init_db
from app.database import Base, engine, session_scope
from app.models import Report, ReportItem
from app.services.ai_rss_pipeline import AiRssDailyPipeline
from app.utils import now_local
import main


class AiRssPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=engine)
        init_db()

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        init_db()
        self.client = TestClient(main.app)

    def test_ai_rss_pipeline_creates_ai_report(self):
        pipeline = AiRssDailyPipeline()
        recent = now_local() - timedelta(hours=2)

        async def fake_fetch(*_args, **_kwargs):
            return [
                {
                    "url": "https://example.com/issue-1",
                    "title": "AI 早报 2026-05-12",
                    "snippet": """# AI 早报 2026-05-12

## 概览
### 模型发布
  * OpenAI 发布新模型 [↗](https://openai.com/blog/new-model) `#1`
### 行业动态
  * 新公司发布 AI IDE [↗](https://example.com/ai-ide) `#2`

* * *
## [OpenAI 发布新模型](https://openai.com/blog/new-model) `#1`

> OpenAI 发布了新模型，强调推理与多模态能力提升。

相关链接：
  * https://platform.openai.com/docs

* * *
## [新公司发布 AI IDE](https://example.com/ai-ide) `#2`

> 一家创业公司推出 AI IDE 产品，主打编码自动化。
""",
                    "published_at": recent,
                    "domain": "example.com",
                    "source_name": "Juya AI Daily",
                    "language": "zh",
                },
            ]

        with patch("app.services.ai_rss_pipeline.fetch_feed_entries", new=fake_fetch):
            with session_scope() as session:
                report = asyncio.run(pipeline.run(session))
                report_id = report.id

        with session_scope() as session:
            report = session.get(Report, report_id)
            assert report is not None
            self.assertEqual(report.report_type, "ai")
            self.assertEqual(report.pipeline_version, "ai-rss-v2")
            self.assertEqual(report.status, "complete")
            self.assertEqual(len(report.items), 2)
            self.assertTrue(all(item.decision_trace.get("category") == "AI" for item in report.items))
            self.assertEqual(report.items[0].source_url, "https://openai.com/blog/new-model")
            self.assertEqual(report.items[0].decision_trace.get("source_tier"), "A")

    def test_combined_today_report_merges_global_and_ai_items(self):
        today = now_local().date()
        created_at = datetime(2026, 5, 12, 8, 0, tzinfo=UTC)
        with session_scope() as session:
            global_report = Report(
                report_date=today,
                status="complete",
                title="高分子日报",
                markdown_content="# 高分子日报",
                summary="主日报摘要",
                pipeline_version="native-v2",
                report_type="global",
            )
            ai_report = Report(
                report_date=today,
                status="complete",
                title="AI 日报",
                markdown_content="# AI 日报",
                summary="AI 日报摘要",
                pipeline_version="ai-rss-v1",
                report_type="ai",
            )
            session.add(global_report)
            session.add(ai_report)
            session.flush()
            session.add(
                ReportItem(
                    report_id=global_report.id,
                    section="industry",
                    rank=1,
                    title="高分子设备升级",
                    source_name="Example Global",
                    source_url="https://example.com/global",
                    published_at=created_at,
                    summary="主日报条目",
                    research_signal="主日报信号",
                    selected_for_publish=True,
                    window_bucket="primary_24h",
                    citations=[],
                    combined_score=0.8,
                    decision_trace={"category": "高材制造"},
                    language="zh",
                )
            )
            session.add(
                ReportItem(
                    report_id=ai_report.id,
                    section="industry",
                    rank=1,
                    title="AI 产品发布",
                    source_name="Juya AI Daily",
                    source_url="https://example.com/ai",
                    published_at=created_at,
                    summary="AI 条目",
                    research_signal="AI 信号",
                    selected_for_publish=True,
                    window_bucket="primary_24h",
                    citations=[],
                    combined_score=0.7,
                    decision_trace={"category": "其他"},
                    language="zh",
                )
            )
            session.commit()

        response = self.client.get("/api/reports/today?view=combined")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["report_type"], "combined")
        self.assertEqual(len(payload["items"]), 2)
        self.assertIn("AI 日报摘要", payload["summary"])
        ai_items = [item for item in payload["items"] if item["decision_trace"].get("category") == "AI"]
        self.assertEqual(len(ai_items), 1)


if __name__ == "__main__":
    unittest.main()
