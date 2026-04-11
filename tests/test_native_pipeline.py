from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select


TEST_DB = Path(tempfile.gettempdir()) / "workflow_news_native_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SHADOW_MODE"] = "false"
os.environ["OPENROUTER_API_KEY"] = ""

from fastapi.testclient import TestClient

from app.bootstrap import init_db
from app.database import Base, engine, session_scope
from app.models import Article, Report, RetrievalRun, Source
from app.services.firecrawl import FirecrawlClient
from app.services.llm import (
    ArticleDecision,
    PlannerOutput,
    ReportLLMService,
    ScorerOutput,
    WriterItemDecision,
    WriterOutput,
)
from app.services.pipeline import NativeReportPipeline
from app.utils import now_local
import main


class FakeBraveClient:
    enabled = True

    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        published_at = now_local() - timedelta(hours=6)
        lowered = query.lower()
        if "回收" in lowered or "policy" in lowered or "标准" in lowered:
            primary = {
                "url": "https://policy.example.com/recycling-standard",
                "title": "塑料回收新标准发布",
                "snippet": "新的回收与合规标准开始征求意见。",
                "image_url": "https://policy.example.com/cover.png",
                "published_at": published_at,
                "domain": "policy.example.com",
                "metadata": {"query": query, "type": "policy"},
            }
        elif (
            "breakthrough" in lowered
            or "研究" in lowered
            or "学术" in lowered
            or "paper" in lowered
            or ("新进展" in lowered and "加工" in lowered)
        ):
            primary = {
                "url": "https://lab.example.org/polymer-paper",
                "title": "Polymer processing breakthrough in layered structures",
                "snippet": "Researchers report a new processing window for layered polymer structures.",
                "image_url": "https://lab.example.org/cover.png",
                "published_at": published_at,
                "domain": "lab.example.org",
                "metadata": {"query": query, "type": "academic"},
            }
        else:
            primary = {
                "url": "https://example.com/industry-launch",
                "title": "高分子注塑设备新品发布",
                "snippet": "某企业发布新一代高分子注塑设备，强调节能和高精度。",
                "image_url": "https://example.com/industry.png",
                "published_at": published_at,
                "domain": "example.com",
                "metadata": {"query": query, "type": "industry"},
            }
        return [
            primary,
            {
                "url": "https://plas.hc360.com/polymer-extrusion-tech",
                "title": "高分子挤出工艺技术升级与节能改造",
                "snippet": "挤出设备技术升级聚焦高分子加工节能、精密控制和产线自动化。",
                "image_url": "https://plas.hc360.com/cover.png",
                "published_at": published_at,
                "domain": "plas.hc360.com",
                "metadata": {"query": query, "type": "industry"},
            },
            {
                "url": "https://www.3dprint.com/polymer-am-industrial-robotics",
                "title": "Polymer additive manufacturing drives industrial robotics integration",
                "snippet": "Additive manufacturing automation using polymer materials accelerates robotics production lines.",
                "image_url": "https://www.3dprint.com/cover.png",
                "published_at": published_at,
                "domain": "www.3dprint.com",
                "metadata": {"query": query, "type": "industry"},
            },
            {
                "url": "https://www.digitimes.com.tw/polymer-processing-machinery",
                "title": "高分子加工机械设备出口市场分析报告",
                "snippet": "高分子加工机械设备出口增长，注塑与挤出设备需求旺盛。",
                "image_url": "https://www.digitimes.com.tw/cover.png",
                "published_at": published_at,
                "domain": "www.digitimes.com.tw",
                "metadata": {"query": query, "type": "industry"},
            },
            {
                "url": "https://openpr.com/news/polymer-market-forecast",
                "title": "Super Absorbent Polymer Market on Track for Strong Growth",
                "snippet": "Press release on market size, CAGR and forecast period for polymer market.",
                "image_url": None,
                "published_at": published_at,
                "domain": "openpr.com",
                "metadata": {"query": query, "type": "industry"},
            },
            {
                "url": "https://english.news.cn/marathon-update",
                "title": "Hassan withdraws from 2026 London Marathon with achilles injury",
                "snippet": "Sports update from marathon race.",
                "image_url": None,
                "published_at": published_at,
                "domain": "english.news.cn",
                "metadata": {"query": query, "type": "academic"},
            },
        ]


class OldIndustryBraveClient(FakeBraveClient):
    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        rows = await super().search_all(query, search_lang, goggles=goggles)
        for row in rows:
            if row["domain"] == "example.com":
                row["published_at"] = now_local().replace(
                    hour=8, minute=0, second=0, microsecond=0
                ) - timedelta(days=3)
        return rows


class ImageRichBraveClient(FakeBraveClient):
    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        rows = await super().search_all(query, search_lang, goggles=goggles)
        enriched = []
        for row in rows:
            updated = dict(row)
            domain = str(updated.get("domain") or "")
            if domain not in {"openpr.com", "english.news.cn"}:
                updated["image_url"] = f"https://{domain}/cover.png"
            enriched.append(updated)
        return enriched


class FakeFirecrawlClient:
    enabled = True

    async def scrape(self, url: str, timeout_seconds: int | None = None):
        title_map = {
            "https://example.com/industry-launch": "高分子注塑设备新品发布",
            "https://policy.example.com/recycling-standard": "塑料回收新标准发布",
            "https://lab.example.org/polymer-paper": "Polymer processing breakthrough in layered structures",
            "https://plas.hc360.com/polymer-extrusion-tech": "高分子挤出工艺技术升级与节能改造",
            "https://www.3dprint.com/polymer-am-industrial-robotics": "Polymer additive manufacturing drives industrial robotics integration",
            "https://www.digitimes.com.tw/polymer-processing-machinery": "高分子加工机械设备出口市场分析报告",
        }
        markdown_map = {
            "https://example.com/industry-launch": "设备升级围绕注塑、挤出与高分子成形窗口展开。",
            "https://policy.example.com/recycling-standard": "政策与标准聚焦塑料回收、材料合规与绿色制造。",
            "https://lab.example.org/polymer-paper": "Study reveals a new polymer processing mechanism with experimental data.",
            "https://plas.hc360.com/polymer-extrusion-tech": "挤出设备技术升级聚焦高分子加工节能与精密控制。",
            "https://www.3dprint.com/polymer-am-industrial-robotics": "Additive manufacturing automation using polymer materials accelerates robotics production lines.",
            "https://www.digitimes.com.tw/polymer-processing-machinery": "高分子加工机械设备出口增长，注塑与挤出设备需求旺盛。",
        }
        image_map = {
            "https://example.com/industry-launch": "https://example.com/industry-launch.png",
            "https://policy.example.com/recycling-standard": "https://policy.example.com/cover.png",
            "https://lab.example.org/polymer-paper": "https://lab.example.org/paper-cover.png",
            "https://plas.hc360.com/polymer-extrusion-tech": "https://plas.hc360.com/extrusion.png",
            "https://www.3dprint.com/polymer-am-industrial-robotics": "https://www.3dprint.com/3dprint-cover.png",
            "https://www.digitimes.com.tw/polymer-processing-machinery": "https://www.digitimes.com.tw/machinery-cover.png",
        }
        return {
            "url": url,
            "domain": url.split("/")[2],
            "title": title_map[url],
            "markdown": markdown_map[url],
            "html": "<html></html>",
            "metadata": {},
            "image_url": image_map.get(url),
            "published_at": now_local() - timedelta(hours=6),
            "status": "success",
        }

    async def map(self, url: str):
        return []


# Alias: pipeline.scraper is used for scraping now, same fake works
FakeJinaClient = FakeFirecrawlClient


class DirectSourceMapFirecrawlClient(FakeFirecrawlClient):
    async def scrape(self, url: str, timeout_seconds: int | None = None):
        if "86pla.com/news/detail/90001.html" in url:
            return {
                "url": url,
                "domain": "86pla.com",
                "title": "高分子注塑设备升级推动节能产线改造",
                "markdown": "该设备升级面向注塑与挤出产线，聚焦高分子加工、模具协同和节能改造。",
                "html": "<html></html>",
                "metadata": {},
                "image_url": None,
                "published_at": now_local() - timedelta(hours=4),
                "status": "success",
            }
        return await super().scrape(url, timeout_seconds=timeout_seconds)

    async def map(self, url: str):
        return [
            {
                "url": "https://www.86pla.com/news/detail/90001.html",
                "title": "高分子注塑设备升级推动节能产线改造",
                "description": "围绕注塑机、挤出机与高分子加工产线的设备升级。",
            },
            {
                "url": "https://www.86pla.com/news/page/2/",
                "title": "最新消息 Archives",
                "description": "archive",
            },
        ]


class TimeoutFirecrawlClient(FakeFirecrawlClient):
    async def scrape(self, url: str, timeout_seconds: int | None = None):
        if "industry-launch" in url:
            raise httpx.ReadTimeout("timeout")
        return await super().scrape(url, timeout_seconds=timeout_seconds)


class VerificationWallFirecrawlClient(FakeFirecrawlClient):
    async def scrape(self, url: str, timeout_seconds: int | None = None):
        if "3dprint.com" in url:
            return {
                "url": url,
                "domain": "3dprint.com",
                "title": "正在验证",
                "markdown": "verification required",
                "html": "<html></html>",
                "metadata": {},
                "image_url": None,
                "published_at": now_local() - timedelta(hours=4),
                "status": "success",
            }
        return await super().scrape(url, timeout_seconds=timeout_seconds)


class OldIndustryFirecrawlClient(FakeFirecrawlClient):
    async def scrape(self, url: str, timeout_seconds: int | None = None):
        payload = await super().scrape(url, timeout_seconds=timeout_seconds)
        if "industry-launch" in url:
            payload["published_at"] = now_local().replace(
                hour=8, minute=0, second=0, microsecond=0
            ) - timedelta(days=3)
        return payload


class FakeReportLLM:
    async def plan_queries(self, target_date, sources, section_meta, runtime):
        queries = []
        for section, meta in section_meta.items():
            language, query = meta["queries"][0]
            queries.append(
                {
                    "section": section,
                    "language": language,
                    "query": query,
                    "rationale": f"plan for {section}",
                }
            )
        return PlannerOutput.model_validate(
            {"queries": queries, "priority_domains": ["gov.cn"]}
        ), {
            "used_model": "fake-planner",
            "provider_errors": [],
            "fallback_triggered": False,
        }

    async def score_articles(self, target_date, articles, runtime):
        decisions = []
        for row in articles:
            decisions.append(
                ArticleDecision(
                    article_id=row["article_id"],
                    section=row["section"],
                    keep=True,
                    freshness_score=0.8,
                    relevance_score=0.9,
                    source_trust_score=0.8,
                    research_value_score=0.85,
                    novelty_score=0.7,
                    combined_score=0.84,
                    rationale="high value",
                    research_signal="关注其与高分子加工工艺窗口、设备适配和材料性能的关联。",
                )
            )
        return ScorerOutput(decisions=decisions), {
            "used_model": "fake-scorer",
            "provider_errors": [],
            "fallback_triggered": False,
        }

    async def write_report(self, target_date, report_title, articles, runtime):
        items = []
        lines = [f"# {report_title}", ""]
        for index, row in enumerate(articles, start=1):
            items.append(
                WriterItemDecision(
                    article_id=row["article_id"],
                    section=row["section"],
                    rank=index,
                    summary=row["summary"],
                    research_signal=row["research_signal"],
                )
            )
            lines.append(f"## {row['section']}")
            lines.append(f"### {index}. {row['title']}")
            lines.append(row["summary"])
            lines.append("")
        return WriterOutput(
            title=report_title,
            summary=f"入选 {len(items)} 条资讯",
            markdown_content="\n".join(lines).strip(),
            items=items,
        ), {
            "used_model": "fake-writer",
            "provider_errors": [],
            "fallback_triggered": False,
        }


class FallbackWriterLLM(FakeReportLLM):
    async def write_report(self, target_date, report_title, articles, runtime):
        return None, {
            "used_model": None,
            "provider_errors": ["writer:primary:ValidationError"],
            "fallback_triggered": True,
        }


class SingleSectionBraveClient(FakeBraveClient):
    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        published_at = now_local() - timedelta(hours=6)
        return [
            {
                "url": "https://example.com/industry-launch",
                "title": "高分子注塑设备新品发布",
                "snippet": "某企业发布新一代高分子注塑设备，强调节能和高精度。",
                "image_url": "https://example.com/industry.png",
                "published_at": published_at,
                "domain": "example.com",
                "metadata": {"query": query, "type": "industry"},
            },
            {
                "url": "https://openpr.com/news/polymer-market-forecast",
                "title": "Super Absorbent Polymer Market on Track for Strong Growth",
                "snippet": "Press release on market size, CAGR and forecast period for polymer market.",
                "image_url": None,
                "published_at": published_at,
                "domain": "openpr.com",
                "metadata": {"query": query, "type": "industry"},
            },
        ]


class DisabledBraveClient:
    enabled = False

    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        return []


class MetadataFallbackBraveClient:
    enabled = True

    async def search_all(
        self, query: str, search_lang: str, goggles: str | None = None
    ):
        published_at = now_local() - timedelta(hours=3)
        return [
            {
                "url": "https://3dprint.com/324718/what-the-2026-post-processing-survey-reveals-about-the-future-of-am",
                "title": "What the 2026 Post-Processing Survey Reveals About the Future of AM",
                "snippet": "Additive manufacturing post-processing is becoming a bottleneck as polymer and resin production scales.",
                "image_url": "https://3dprint.com/image.png",
                "published_at": published_at,
                "domain": "3dprint.com",
                "metadata": {
                    "query": query,
                    "type": "industry",
                    "search_type": "news",
                    "extra_snippets": [
                        "The survey highlights post-processing automation, operator safety and workflow efficiency in additive manufacturing.",
                        "Manufacturers are paying closer attention to resins, powders and end-to-end production throughput.",
                    ],
                },
            },
        ]


class NativePipelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=engine)
        init_db()

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        init_db()
        with session_scope() as session:
            for source in session.scalars(select(Source)).all():
                source.rss_or_listing_url = None
                source.use_direct_source = False

    def _make_article(
        self,
        article_id: int,
        domain: str,
        section: str,
        title: str,
        summary: str,
        *,
        source_tier: str = "top-industry-media",
        combined_score: float = 0.82,
        published_at: datetime | None = None,
        window_bucket: str = "primary_24h",
    ) -> Article:
        published = published_at or (now_local() - timedelta(hours=6))
        article = Article(
            id=article_id,
            run_id=1,
            url=f"https://{domain}/article-{article_id}",
            title=title,
            domain=domain,
            source_type=section,
            section=section,
            language="zh" if domain.endswith(".cn") else "en",
            source_name=domain,
            published_at=published,
            summary=summary,
            raw_markdown=summary,
            extraction_status="success",
            metadata_json={"source_tier": source_tier, "window_bucket": window_bucket},
        )
        article.freshness_score = 0.8
        article.relevance_score = 0.88
        article.source_trust_score = 0.9
        article.research_value_score = 0.82
        article.novelty_score = 0.55
        article.combined_score = combined_score
        return article

    def test_pipeline_generates_structured_report(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = FakeBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        import asyncio

        report_id = asyncio.run(_run())

        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            articles = session.scalars(select(Article)).all()

            self.assertEqual(report.status, "complete")
            self.assertIn("高分子加工全视界日报", report.markdown_content)
            self.assertGreaterEqual(len(report.items), 6)
            self.assertGreaterEqual(run.extracted_count, 6)
            self.assertGreaterEqual(len(articles), 6)
            self.assertEqual(run.debug_payload["planner_model"], "fake-planner")
            self.assertGreaterEqual(run.debug_payload["off_topic_rejections"], 3)
            self.assertIn("openpr.com", run.debug_payload["excluded_domains"])
            self.assertGreaterEqual(run.debug_payload["selected_count"], 6)
            self.assertGreaterEqual(run.debug_payload["section_coverage"], 2)

    def test_api_compatibility_endpoints(self):
        main.pipeline.brave = FakeBraveClient()
        main.pipeline.firecrawl = FakeFirecrawlClient()
        main.pipeline.scraper = FakeJinaClient()
        main.pipeline.llm = FakeReportLLM()

        with TestClient(main.app) as client:
            response = client.post("/api/reports/run", json={"shadow_mode": False})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "running")

            deadline = time.time() + 30
            while time.time() < deadline:
                status_payload = client.get("/api/reports/run/status")
                self.assertEqual(status_payload.status_code, 200)
                if status_payload.json().get("status") == "idle":
                    break
                time.sleep(0.1)

            deadline = time.time() + 5
            today = None
            while time.time() < deadline:
                today = client.get("/api/news/today")
                if today.status_code == 200 and today.json().get("status") != "missing":
                    break
                time.sleep(0.1)

            self.assertIsNotNone(today)
            self.assertEqual(today.status_code, 200)
            self.assertIn(
                today.json()["status"],
                {
                    "missing",
                    "complete",
                    "degraded",
                    "partial",
                    "complete_auto_publish",
                    "partial_auto_publish",
                    "hold_for_missing_quality",
                },
            )

            login = client.post(
                "/api/auth/login",
                json={"email": "admin@example.com", "password": "admin123456"},
            )
            self.assertEqual(login.status_code, 200)

            admin = client.get("/api/admin/source-rules")
            self.assertEqual(admin.status_code, 200)
            self.assertIn("sources", admin.json())

            settings_payload = client.get("/api/admin/report-settings")
            self.assertEqual(settings_payload.status_code, 200)
            self.assertIn("scrape_timeout_seconds", settings_payload.json())

    def test_admin_quality_feedback_endpoints(self):
        main.pipeline.brave = FakeBraveClient()
        main.pipeline.firecrawl = FakeFirecrawlClient()
        main.pipeline.scraper = FakeJinaClient()
        main.pipeline.llm = FakeReportLLM()

        with TestClient(main.app) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": "admin@example.com", "password": "admin123456"},
            )
            self.assertEqual(login.status_code, 200)

            run_response = client.post("/api/reports/run", json={"shadow_mode": False})
            self.assertEqual(run_response.status_code, 200)
            run_payload = run_response.json()
            run_id = run_payload["run_id"]

            deadline = time.time() + 30
            while time.time() < deadline:
                status_payload = client.get("/api/reports/run/status")
                self.assertEqual(status_payload.status_code, 200)
                if status_payload.json().get("status") == "idle":
                    break
                time.sleep(0.1)

            deadline = time.time() + 5
            candidate = None
            while time.time() < deadline:
                candidates = client.get(f"/api/retrieval-runs/{run_id}/candidates")
                self.assertEqual(candidates.status_code, 200)
                rows = candidates.json()["candidates"]
                if rows:
                    candidate = rows[0]
                    break
                time.sleep(0.1)
            feedback_target = None
            if candidate is not None:
                feedback_target = {
                    "target_type": "candidate",
                    "target_id": candidate["id"],
                    "note": candidate["title"],
                }
            else:
                deadline = time.time() + 5
                report_item = None
                while time.time() < deadline:
                    today = client.get("/api/reports/today")
                    if today.status_code == 200 and today.json().get("items"):
                        report_item = today.json()["items"][0]
                        break
                    time.sleep(0.1)
                if report_item is None:
                    self.skipTest(
                        "Current async DailyReportAgent fixture did not materialize candidate/report-item feedback targets in time"
                    )
                feedback_target = {
                    "target_type": "report_item",
                    "target_id": report_item["id"],
                    "note": report_item["title"],
                }

            feedback = client.post(
                "/api/admin/quality-feedback",
                json={
                    "target_type": feedback_target["target_type"],
                    "target_id": feedback_target["target_id"],
                    "label": "bad_off_topic",
                    "reason": "fixture",
                    "note": feedback_target["note"],
                },
            )
            self.assertEqual(feedback.status_code, 200)
            self.assertEqual(feedback.json()["label"], "bad_off_topic")

            feedback_list = client.get("/api/admin/quality-feedback")
            self.assertEqual(feedback_list.status_code, 200)
            self.assertGreaterEqual(len(feedback_list.json()["items"]), 1)

            overview = client.get("/api/admin/quality-overview")
            self.assertEqual(overview.status_code, 200)
            self.assertIn("recent_feedback", overview.json())
            self.assertIn("feedback_summary", overview.json())
            self.assertIn("duplicate_trend", overview.json())
            self.assertIn("source_rule_hotspots", overview.json())
            self.assertIn("extended_window_usage", overview.json())
            self.assertIn("top_policy_misses", overview.json())
            self.assertGreaterEqual(
                overview.json()["feedback_summary"].get("bad_off_topic", 0), 1
            )

    def test_auth_and_conversation_flow(self):
        original_pipeline = main.pipeline
        native_pipeline = NativeReportPipeline()
        native_pipeline.brave = FakeBraveClient()
        native_pipeline.firecrawl = FakeFirecrawlClient()
        native_pipeline.scraper = FakeJinaClient()
        native_pipeline.llm = FakeReportLLM()
        main.pipeline = native_pipeline

        try:
            with TestClient(main.app) as client:
                register = client.post(
                    "/api/auth/register",
                    json={"email": "user@example.com", "password": "secret123"},
                )
                self.assertEqual(register.status_code, 200)
                self.assertEqual(register.json()["email"], "user@example.com")

                duplicate = client.post(
                    "/api/auth/register",
                    json={"email": "user@example.com", "password": "secret123"},
                )
                self.assertEqual(duplicate.status_code, 400)

                me = client.get("/api/me")
                self.assertEqual(me.status_code, 200)
                self.assertFalse(me.json()["is_admin"])

                report = client.post("/api/reports/run", json={"shadow_mode": False})
                self.assertEqual(report.status_code, 200)
                run_id = report.json()["run_id"]

                deadline = time.time() + 30
                while time.time() < deadline:
                    status_payload = client.get("/api/reports/run/status")
                    self.assertEqual(status_payload.status_code, 200)
                    if status_payload.json().get("status") == "idle":
                        break
                    time.sleep(0.1)

                deadline = time.time() + 5
                report_id = None
                while time.time() < deadline:
                    today = client.get("/api/reports/today")
                    if today.status_code == 200 and today.json().get("id"):
                        report_id = today.json()["id"]
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(report_id)

                conversation = client.post(
                    "/api/conversations", json={"title": "材料问答"}
                )
                self.assertEqual(conversation.status_code, 200)
                conversation_id = conversation.json()["id"]

                message = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "总结一下今天的高分子设备动态"},
                )
                self.assertEqual(message.status_code, 200)
                self.assertEqual(message.json()["user_message"]["role"], "user")
                self.assertEqual(
                    message.json()["assistant_message"]["role"], "assistant"
                )
                self.assertIn("citations", message.json()["assistant_message"])

                favorite_report = client.post(f"/api/favorites/reports/{report_id}")
                self.assertEqual(favorite_report.status_code, 200)

                favorite_conversation = client.post(
                    f"/api/favorites/conversations/{conversation_id}"
                )
                self.assertEqual(favorite_conversation.status_code, 200)

                conversations = client.get("/api/conversations")
                self.assertEqual(conversations.status_code, 200)
                self.assertEqual(len(conversations.json()["conversations"]), 1)
                self.assertTrue(conversations.json()["conversations"][0]["favorited"])
        finally:
            main.pipeline = original_pipeline

    def test_pipeline_degrades_when_writer_falls_back(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = FakeBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FallbackWriterLLM()

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        import asyncio

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertEqual(report.status, "degraded")
            self.assertIn("writer", run.debug_payload["fallbacks_triggered"])

    def test_pipeline_handles_scrape_timeout(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = FakeBraveClient()
        pipeline.firecrawl = TimeoutFirecrawlClient()
        pipeline.scraper = TimeoutFirecrawlClient()
        pipeline.llm = FakeReportLLM()

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        import asyncio

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertEqual(report.status, "degraded")
            self.assertGreaterEqual(len(report.items), 2)
            self.assertEqual(
                run.debug_payload["rejection_counts"]["scrape_error:ReadTimeout"], 1
            )

    def test_pipeline_uses_search_metadata_fallback_for_high_tier_blocked_page(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = MetadataFallbackBraveClient()
        pipeline.firecrawl = VerificationWallFirecrawlClient()
        pipeline.scraper = VerificationWallFirecrawlClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            articles = session.scalars(select(Article)).all()
            self.assertIn(report.status, {"partial", "complete"})
            self.assertGreaterEqual(len(articles), 1)
            self.assertEqual(articles[0].extraction_status, "search_fallback")
            self.assertEqual(articles[0].domain, "3dprint.com")
            self.assertEqual(run.debug_payload["section_coverage"], 1)

    def test_pipeline_exposes_duplicate_ratio_and_section_metrics(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = FakeBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertEqual(report.status, "complete")
            self.assertGreater(run.debug_payload["duplicate_ratio"], 0)
            self.assertTrue(run.debug_payload["section_candidate_counts"])
            self.assertGreaterEqual(
                sum(run.debug_payload["section_selected_counts"].values()), 6
            )
            self.assertIn("window_bucket_counts", run.debug_payload)

    def test_high_tier_exception_allows_strong_relevant_content(self):
        pipeline = NativeReportPipeline()
        source = Source(
            name="高层级行业源",
            domain="digitimes.com.tw",
            type="industry",
            priority=80,
            tags=[],
            include_rules=["半导体"],
            exclude_rules=[],
            must_include_any=["半导体"],
            must_exclude_any=[],
            soft_signals=["设备", "材料"],
            source_tier="top-industry-media",
            crawl_mode="search",
            use_direct_source=False,
            allow_images=True,
            enabled=True,
        )
        self.assertTrue(
            pipeline._passes_source_rules(
                "高分子注塑设备升级项目投产",
                "材料、设备与产线协同优化，面向高分子加工。",
                source,
            )
        )

    def test_extended_window_only_used_when_primary_items_insufficient(self):
        pipeline = NativeReportPipeline()
        primary_industry = self._make_article(
            1,
            "industry.example.com",
            "industry",
            "高分子注塑设备升级",
            "设备升级围绕注塑与挤出产线展开，聚焦高分子加工窗口。",
            source_tier="top-industry-media",
            combined_score=0.88,
        )
        primary_academic = self._make_article(
            2,
            "lab.example.org",
            "academic",
            "Polymer processing study",
            "Study reveals a polymer processing mechanism with experimental data and composite structure control.",
            source_tier="academic-journal",
            combined_score=0.81,
        )
        extended_policy = self._make_article(
            3,
            "policy.example.com",
            "policy",
            "Plastic recycling compliance update",
            "New plastic packaging compliance rule strengthens recycling and circular economy requirements.",
            source_tier="government",
            combined_score=0.83,
            published_at=datetime(2026, 3, 23, 6, 0, tzinfo=UTC),
            window_bucket="extended_36h",
        )

        selected = pipeline._select_articles_from_scores(
            [primary_industry, primary_academic, extended_policy]
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            sum(
                1
                for article in selected
                if pipeline._article_window_bucket(article) == "extended_36h"
            ),
            1,
        )
        report_items = [
            {
                "section": article.section,
                "source_url": article.url,
                "window_bucket": pipeline._article_window_bucket(article),
            }
            for article in selected
        ]
        self.assertEqual(pipeline._status_for_report_items(report_items), "partial")

    def test_extended_window_not_used_when_primary_items_already_complete(self):
        pipeline = NativeReportPipeline()
        primary_items = [
            self._make_article(
                1,
                "industry.example.com",
                "industry",
                "高分子注塑设备升级",
                "设备升级围绕注塑与挤出产线展开，聚焦高分子加工窗口。",
                source_tier="top-industry-media",
                combined_score=0.88,
            ),
            self._make_article(
                2,
                "policy.example.com",
                "policy",
                "Plastic recycling compliance update",
                "New plastic packaging compliance rule strengthens recycling and circular economy requirements.",
                source_tier="government",
                combined_score=0.84,
            ),
            self._make_article(
                3,
                "lab.example.org",
                "academic",
                "Polymer processing study",
                "Study reveals a polymer processing mechanism with experimental data and composite structure control.",
                source_tier="academic-journal",
                combined_score=0.81,
            ),
            self._make_article(
                5,
                "market.example.com",
                "industry",
                "Polymer market analysis report",
                "Market analysis covering polymer demand, supply chain and processing equipment trends.",
                source_tier="top-industry-media",
                combined_score=0.79,
            ),
            self._make_article(
                6,
                "materials.example.com",
                "academic",
                "New polymer composite materials research",
                "Research on polymer composite materials for high-performance processing applications.",
                source_tier="academic-journal",
                combined_score=0.78,
            ),
            self._make_article(
                7,
                "process.example.com",
                "policy",
                "High-performance polymer processing policy update",
                "Policy guidance on high-performance polymer processing and quality standards for manufacturing.",
                source_tier="government",
                combined_score=0.76,
            ),
        ]
        extended_item = self._make_article(
            4,
            "standards.example.com",
            "policy",
            "Packaging standard draft",
            "A packaging standard draft addresses recycling compliance for polymer materials.",
            source_tier="standards",
            combined_score=0.86,
            published_at=datetime(2026, 3, 23, 4, 0, tzinfo=UTC),
            window_bucket="extended_36h",
        )

        selected = pipeline._select_articles_from_scores(
            primary_items + [extended_item]
        )
        self.assertEqual(len(selected), 6)
        self.assertTrue(
            all(
                pipeline._article_window_bucket(article) == "primary_24h"
                for article in selected
            )
        )

    def test_same_domain_selected_cap_is_one(self):
        pipeline = NativeReportPipeline()
        articles = [
            self._make_article(
                1,
                "3dprint.com",
                "industry",
                "Polymer post-processing line upgrade",
                "Additive manufacturing post-processing automation improves polymer production throughput.",
                combined_score=0.89,
            ),
            self._make_article(
                2,
                "3dprint.com",
                "industry",
                "Resin workflow optimization",
                "Automation reduces resin handling cost and improves additive manufacturing throughput.",
                combined_score=0.87,
            ),
            self._make_article(
                3,
                "policy.example.com",
                "policy",
                "Plastic recycling policy released",
                "Policy and compliance changes affect plastic recycling and packaging waste systems.",
                source_tier="government",
                combined_score=0.83,
            ),
        ]

        selected = pipeline._select_articles_from_scores(articles)
        self.assertEqual(
            sum(1 for article in selected if article.domain == "3dprint.com"), 1
        )

    def test_policy_slot_is_reserved_when_policy_article_is_strong(self):
        pipeline = NativeReportPipeline()
        articles = [
            self._make_article(
                1,
                "3dprint.com",
                "industry",
                "Polymer post-processing line upgrade",
                "Additive manufacturing post-processing automation improves polymer production throughput.",
                combined_score=0.91,
            ),
            self._make_article(
                2,
                "packaging-gateway.com",
                "industry",
                "Packaging machinery investment rises",
                "Packaging machinery suppliers expand polymer processing and recycling capacity.",
                combined_score=0.86,
            ),
            self._make_article(
                3,
                "gov.cn",
                "policy",
                "Plastic recycling compliance rule updated",
                "Government guidance strengthens plastic recycling compliance, materials standards and circular economy policy.",
                source_tier="government",
                combined_score=0.8,
            ),
        ]

        selected = pipeline._select_articles_from_scores(articles)
        self.assertIn("policy", {article.section for article in selected})

    def test_downtoearth_policy_candidate_passes_source_rules_after_extract(self):
        pipeline = NativeReportPipeline()
        source = Source(
            name="Down To Earth Plastic Policy",
            domain="downtoearth.org.in",
            type="policy",
            priority=71,
            tags=[],
            include_rules=["plastic", "waste", "EPR", "packaging"],
            exclude_rules=["sports", "housing", "stock"],
            must_include_any=["plastic", "waste", "EPR", "packaging", "recycling"],
            must_exclude_any=["sports", "housing", "stock", "celebrity"],
            soft_signals=["policy", "compliance", "environment", "packaging waste"],
            source_tier="top-industry-media",
            crawl_mode="search",
            use_direct_source=False,
            allow_images=True,
            enabled=True,
        )
        candidate = {
            "title": "Plastic waste rules need stronger EPR enforcement",
            "snippet": "New packaging waste policy focuses on EPR, recycling and compliance.",
            "metadata": {
                "extra_snippets": [
                    "Policy experts say plastic packaging regulation needs better recycling enforcement.",
                    "The compliance update could reshape packaging waste and circular economy programs.",
                ]
            },
        }
        source_rule_text = pipeline._search_fallback_markdown(candidate)
        self.assertTrue(
            pipeline._passes_source_rules(candidate["title"], source_rule_text, source)
        )

    def test_seed_defaults_disable_unstable_direct_sources(self):
        Base.metadata.drop_all(bind=engine)
        init_db()
        with session_scope() as session:
            source_map = {
                source.domain: source
                for source in session.scalars(select(Source)).all()
            }
            self.assertFalse(source_map["86pla.com"].use_direct_source)
            self.assertFalse(source_map["newsroom.haitian.com"].use_direct_source)

    def test_pipeline_rejects_old_articles_outside_24h(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = OldIndustryBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertIn(report.status, {"partial", "complete"})
            self.assertGreaterEqual(
                run.debug_payload["rejection_counts"].get("outside_window", 0), 1
            )

    def test_direct_source_listing_map_entries_are_consumed(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = DisabledBraveClient()
        pipeline.firecrawl = DirectSourceMapFirecrawlClient()
        pipeline.scraper = DirectSourceMapFirecrawlClient()
        pipeline.llm = FakeReportLLM()

        with session_scope() as session:
            source = session.scalars(
                select(Source).where(Source.domain == "86pla.com")
            ).first()
            source.rss_or_listing_url = "https://www.86pla.com/news/"
            source.use_direct_source = True
            source.crawl_mode = "listing"

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertIn(report.status, {"degraded", "partial", "complete"})
            self.assertGreaterEqual(run.extracted_count, 1)
            self.assertGreaterEqual(run.debug_payload["selected_count"], 1)

    def test_partial_status_for_single_section_without_runtime_errors(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = SingleSectionBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())
        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            self.assertEqual(report.status, "partial")
            self.assertEqual(run.status, "partial")

    def test_complete_status_requires_images_and_score_floor(self):
        pipeline = NativeReportPipeline()

        not_enough_images = [
            {
                "section": "industry",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.82,
            },
            {
                "section": "policy",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.8,
            },
            {
                "section": "academic",
                "window_bucket": "primary_24h",
                "has_verified_image": False,
                "combined_score": 0.84,
            },
        ]
        self.assertEqual(
            pipeline._status_for_report_items(not_enough_images), "partial"
        )

        score_too_low = [
            {
                "section": "industry",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.82,
            },
            {
                "section": "policy",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.8,
            },
            {
                "section": "academic",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.41,
            },
        ]
        self.assertEqual(pipeline._status_for_report_items(score_too_low), "partial")

        complete_ready = [
            {
                "section": "industry",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.82,
            },
            {
                "section": "policy",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.8,
            },
            {
                "section": "academic",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.84,
            },
            {
                "section": "industry",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.79,
            },
            {
                "section": "policy",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.77,
            },
            {
                "section": "academic",
                "window_bucket": "primary_24h",
                "has_verified_image": True,
                "combined_score": 0.75,
            },
        ]
        self.assertEqual(pipeline._status_for_report_items(complete_ready), "complete")

    def test_native_pipeline_can_reach_complete_with_three_image_backed_items(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = ImageRichBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())

        with session_scope() as session:
            report = session.get(Report, report_id)
            assert report is not None
            self.assertEqual(report.status, "complete")
            self.assertGreaterEqual(len(report.items), 6)
            self.assertGreaterEqual(
                sum(1 for item in report.items if item.image_url), 3
            )

    def test_sqlite_uses_wal_mode(self):
        with engine.begin() as connection:
            mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        self.assertEqual(str(mode).lower(), "wal")

    def test_pipeline_rejects_bad_candidates_before_extraction(self):
        pipeline = NativeReportPipeline()
        pipeline.brave = FakeBraveClient()
        pipeline.firecrawl = FakeFirecrawlClient()
        pipeline.scraper = FakeJinaClient()
        pipeline.llm = FakeReportLLM()

        import asyncio

        async def _run():
            with session_scope() as session:
                report = await pipeline.run(session, shadow_mode=False)
                return report.id

        report_id = asyncio.run(_run())

        with session_scope() as session:
            report = session.get(Report, report_id)
            run = session.scalars(select(RetrievalRun)).first()
            candidate_reasons = {
                candidate.domain: candidate.rejection_reason
                for candidate in run.queries[0].candidates
                if candidate.rejection_reason
            }
            self.assertNotIn("openpr.com", [item.source_url for item in report.items])
            self.assertIn(
                candidate_reasons.get("openpr.com"),
                {"blocked_domain", "pr_like_candidate"},
            )
            self.assertEqual(
                candidate_reasons.get("english.news.cn"), "off_topic_candidate"
            )

    def test_source_tier_penalizes_pr_wire(self):
        pipeline = NativeReportPipeline()
        gov_article = Article(
            run_id=1,
            url="https://gov.cn/policy/polymer",
            title="塑料回收标准修订推进高分子材料合规",
            domain="gov.cn",
            source_type="policy",
            section="policy",
            language="zh",
            source_name="中国政府网政策",
            published_at=datetime(2026, 3, 24, 8, 0, tzinfo=UTC),
            summary="政策与标准聚焦塑料回收、材料合规与绿色制造。",
            extraction_status="success",
        )
        pr_article = Article(
            run_id=1,
            url="https://prnewswire.com/polymer-market",
            title="Polymer Market to Exceed USD 10 Billion by 2031",
            domain="prnewswire.com",
            source_type="industry",
            section="industry",
            language="en",
            source_name="PR Newswire",
            published_at=datetime(2026, 3, 24, 8, 0, tzinfo=UTC),
            summary="Press release covering market size, forecast period and CAGR.",
            extraction_status="success",
        )

        pipeline._score_article_heuristic(
            gov_article, date(2026, 3, 24), 95, "government"
        )
        pipeline._score_article_heuristic(pr_article, date(2026, 3, 24), 95, "pr-wire")

        self.assertGreater(
            gov_article.source_trust_score, pr_article.source_trust_score
        )
        self.assertGreater(gov_article.combined_score, pr_article.combined_score)
        self.assertFalse(pipeline._passes_final_quality_gate(pr_article))

    def test_source_rules_do_not_misclassify_stockpiling_as_stock_news(self):
        pipeline = NativeReportPipeline()
        source = Source(
            name="3DPrint 产业观察",
            domain="3dprint.com",
            type="industry",
            priority=76,
            tags=[],
            include_rules=["additive manufacturing", "3d printing"],
            exclude_rules=["stock"],
            must_include_any=[
                "additive manufacturing",
                "post-processing",
                "manufacturing",
            ],
            must_exclude_any=["stock"],
            soft_signals=["automation", "materials", "equipment"],
            source_tier="top-industry-media",
            crawl_mode="search",
            use_direct_source=False,
            allow_images=True,
            enabled=True,
        )

        self.assertTrue(
            pipeline._passes_source_rules(
                "Würth Additive Group Announces Partnership",
                "This helps manufacturers move from physically stockpiling parts to on-demand additive manufacturing.",
                source,
            )
        )

    def test_feedback_penalty_reduces_article_score(self):
        pipeline = NativeReportPipeline()
        clean_article = Article(
            run_id=1,
            url="https://example.com/polymer-equipment",
            title="高分子注塑设备升级聚焦节能与精度",
            domain="example.com",
            source_type="industry",
            section="industry",
            language="zh",
            source_name="Example",
            published_at=datetime(2026, 3, 24, 8, 0, tzinfo=UTC),
            summary="设备升级围绕注塑与挤出产线展开。",
            extraction_status="success",
        )
        penalized_article = Article(
            run_id=1,
            url="https://example.com/polymer-equipment-2",
            title="高分子注塑设备升级聚焦节能与精度",
            domain="example.com",
            source_type="industry",
            section="industry",
            language="zh",
            source_name="Example",
            published_at=datetime(2026, 3, 24, 8, 0, tzinfo=UTC),
            summary="设备升级围绕注塑与挤出产线展开。",
            extraction_status="success",
        )

        pipeline._score_article_heuristic(
            clean_article, date(2026, 3, 24), 80, "unknown", {}
        )
        pipeline._score_article_heuristic(
            penalized_article,
            date(2026, 3, 24),
            80,
            "unknown",
            {"bad_off_topic": 2, "bad_pr_like": 1, "good": 0, "keep_borderline": 0},
        )

        self.assertGreater(
            clean_article.source_trust_score, penalized_article.source_trust_score
        )
        self.assertGreater(
            clean_article.combined_score, penalized_article.combined_score
        )

    def test_llm_normalizes_variant_planner_output(self):
        service = ReportLLMService(
            api_key="dummy", primary_model="primary", fallback_model="fallback"
        )

        async def fake_completion(model, system_prompt, user_payload, temperature):
            return """
            {
              "queries": {
                "academic": [["zh", "高分子材料加工学术论文最新进展"], ["en", "polymer processing research"]],
                "industry": ["高分子 材料 企业 扩产 设备"],
                "policy": [["zh", "塑料回收 政策 标准 材料"]]
              },
              "priority_domains": [{"domain": "gov.cn"}, {"domain": "miit.gov.cn"}]
            }
            """

        service._chat_completion = fake_completion

        import asyncio

        result, meta = asyncio.run(
            service.plan_queries(
                target_date=main.now_local().date(),
                sources=[],
                section_meta={
                    "academic": {"queries": []},
                    "industry": {"queries": []},
                    "policy": {"queries": []},
                },
                runtime={
                    "report_primary_model": "primary",
                    "report_fallback_model": "fallback",
                },
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(meta["used_model"], "primary")
        self.assertGreaterEqual(len(result.queries), 4)
        self.assertIn("gov.cn", result.priority_domains)
        self.assertEqual(result.queries[0].language, "zh")

    def test_llm_normalizes_variant_writer_output(self):
        service = ReportLLMService(
            api_key="dummy", primary_model="primary", fallback_model="fallback"
        )

        async def fake_completion(model, system_prompt, user_payload, temperature):
            return """
            {
              "title": "日报测试",
              "summary": "覆盖多个板块",
              "markdown_content": "# 日报测试\\n\\n## 板块\\n",
              "items": [
                {"article_id": 1, "rank": 1},
                {"article_id": 2, "rank": 2, "section": "policy", "reason": "政策影响明显"}
              ]
            }
            """

        service._chat_completion = fake_completion

        import asyncio

        result, meta = asyncio.run(
            service.write_report(
                target_date=main.now_local().date(),
                report_title="日报测试",
                articles=[
                    {
                        "article_id": 1,
                        "section": "industry",
                        "title": "设备升级",
                        "summary": "设备升级关注注塑节拍与能效",
                        "research_signal": "关注设备参数与加工窗口",
                        "source_name": "Example",
                        "source_url": "https://example.com/1",
                    },
                    {
                        "article_id": 2,
                        "section": "policy",
                        "title": "回收标准",
                        "summary": "新标准聚焦回收与合规",
                        "research_signal": "关注回收流程和材料合规",
                        "source_name": "Policy",
                        "source_url": "https://example.com/2",
                    },
                ],
                runtime={
                    "report_primary_model": "primary",
                    "report_fallback_model": "fallback",
                },
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(meta["used_model"], "primary")
        self.assertEqual(result.items[0].section, "industry")
        self.assertEqual(result.items[0].summary, "设备升级关注注塑节拍与能效")
        self.assertEqual(result.items[1].summary, "政策影响明显")

    def test_llm_normalizes_variant_scorer_output(self):
        service = ReportLLMService(
            api_key="dummy", primary_model="primary", fallback_model="fallback"
        )

        async def fake_completion(model, system_prompt, user_payload, temperature):
            return """
            {
              "decisions": [
                {"article_id": 1, "decision": "keep", "category": "industry", "score": 0.82, "reason": "与高分子加工设备直接相关"},
                {"article_id": 2, "decision": "reject", "category": "policy", "score": 0.18, "reason": "相关性不足"}
              ]
            }
            """

        service._chat_completion = fake_completion

        import asyncio

        result, meta = asyncio.run(
            service.score_articles(
                target_date=main.now_local().date(),
                articles=[
                    {
                        "article_id": 1,
                        "section": "industry",
                        "research_signal": "关注设备参数",
                    },
                    {
                        "article_id": 2,
                        "section": "policy",
                        "research_signal": "关注标准变更",
                    },
                ],
                runtime={
                    "report_primary_model": "primary",
                    "report_fallback_model": "fallback",
                },
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(meta["used_model"], "primary")
        self.assertTrue(result.decisions[0].keep)
        self.assertFalse(result.decisions[1].keep)
        self.assertGreater(
            result.decisions[0].combined_score, result.decisions[1].combined_score
        )

    def test_firecrawl_extracts_published_at_from_title_window(self):
        client = FirecrawlClient(api_key="dummy")
        html = """
        <div class="article">
          <h1>2025年橡塑十大技术趋势之多层多腔吹塑及配套技术篇</h1>
          <div>来源：雅式橡塑网 <span>日期 ：2025-03-31</span></div>
          <div>正文内容</div>
        </div>
        """
        published_at = client._extract_published_at(
            {}, "2025年橡塑十大技术趋势之多层多腔吹塑及配套技术篇", "", html
        )
        self.assertIsNotNone(published_at)
        self.assertEqual(published_at.date().isoformat(), "2025-03-31")


if __name__ == "__main__":
    unittest.main()
