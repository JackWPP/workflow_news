from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any
from urllib.parse import urljoin

from sqlalchemy import desc, select

from app.config import settings
from app.models import Article, ArticleCluster, Report, ReportItem, RetrievalCandidate, RetrievalQuery, RetrievalRun, Source
from app.services.brave import BraveSearchClient
from app.services.firecrawl import FirecrawlClient
from app.services.jina_reader import JinaReaderClient
from app.services.scraper import ScraperClient
from app.services.llm import PlannerOutput, ReportLLMService, ScorerOutput, WriterOutput
from app.services.repository import get_report_settings, quality_feedback_domain_stats
from app.services.rss import fetch_feed_entries
from app.utils import (
    canonicalize_url,
    distinct_sections,
    extract_domain,
    infer_language,
    make_cluster_key,
    normalize_title,
    now_local,
    parse_datetime,
    summarize_markdown,
)


SECTION_META = {
    "academic": {
        "heading": "## 🔬 A. 前沿技术与学术",
        "report_name": "前沿技术与学术",
        "queries": [
            ("zh", "高分子 改性 材料 加工 机理 研究"),
            ("zh", "聚合物 加工 流变 结晶 工艺 新进展"),
            ("zh", "高分子 复合材料 成型 研究"),
            ("en", '"polymer processing" mechanism materials'),
        ],
    },
    "industry": {
        "heading": "## 🏭 B. 产业动态与设备",
        "report_name": "产业动态与设备",
        "queries": [
            ("zh", "高分子 材料 企业 扩产 投产 设备"),
            ("zh", "注塑 挤出 吹塑 设备 高分子 材料 产线"),
            ("zh", "橡塑 包装 复合材料 回收 设备"),
            ("en", "site:3dprint.com additive manufacturing post processing polymer"),
        ],
    },
    "policy": {
        "heading": "## 📢 C. 下游应用与政策",
        "report_name": "下游应用与政策",
        "queries": [
            ("en", '"EPR" packaging waste recycling policy plastic'),
            ("en", 'plastic packaging standards compliance recycling'),
            ("zh", "工信 材料 绿色制造 标准 回收"),
            ("zh", "生物基 降解 材料 标准 政策"),
            ("en", 'reprocessing circular economy packaging regulation plastic'),
        ],
    },
}


SOURCE_TYPE_TO_SECTION = {
    "academic": "academic",
    "industry": "industry",
    "policy": "policy",
    "press": "industry",
}

MAX_REPORT_ITEMS = 5
MIN_COMPLETE_ITEMS = 3
MIN_COMPLETE_SECTIONS = 2
MIN_COMPLETE_IMAGES = 3
MIN_PARTIAL_IMAGES = 2
QUALITY_SCORE_THRESHOLD = 0.58
HIGH_CONFIDENCE_SCORE_THRESHOLD = 0.7
MAX_QUERIES_PER_RUN = 12
DOMAIN_CANDIDATE_CAP = 3
PER_DOMAIN_EXTRACTION_CAP = 2
PER_DOMAIN_SELECTED_CAP = 1
PRIMARY_RETRIEVAL_WINDOW_HOURS = 24
EXTENDED_RETRIEVAL_WINDOW_HOURS = 36
MAX_EXTENDED_WINDOW_ITEMS = 2
EXTENDED_WINDOW_BUCKET = "extended_36h"
PRIMARY_WINDOW_BUCKET = "primary_24h"
SECTION_QUERY_LIMITS = {
    "industry": 4,
    "policy": 5,
    "academic": 3,
}
ALLOW_MISSING_PUBLISHED_AT_TIERS = {
    "government",
    "standards",
    "top-industry-media",
    "company-newsroom",
}
EXTENDED_WINDOW_TIERS = {
    "government",
    "standards",
    "top-industry-media",
    "academic-journal",
}

GLOBAL_TOPIC_TERMS = [
    "高分子",
    "塑料",
    "橡胶",
    "复合材料",
    "树脂",
    "改性",
    "薄膜",
    "包装",
    "注塑",
    "挤出",
    "吹塑",
    "成型",
    "回收",
    "再生",
    "生物基",
    "降解",
    "polymer",
    "plastic",
    "rubber",
    "composite",
    "resin",
    "recycling",
    "biodegradable",
    "processing",
    "injection molding",
    "extrusion",
    "additive manufacturing",
    "3d printing",
    "post-processing",
    "powder",
]

INDUSTRY_TERMS = [
    "设备",
    "产线",
    "工厂",
    "扩产",
    "量产",
    "投产",
    "模具",
    "注塑机",
    "挤出机",
    "packaging",
    "factory",
    "capacity",
    "equipment",
    "manufacturing",
    "automation",
    "post-processing",
    "3d printing",
    "additive manufacturing",
]

ACADEMIC_TERMS = [
    "论文",
    "研究",
    "机理",
    "实验",
    "study",
    "research",
    "paper",
    "journal",
    "breakthrough",
    "mechanism",
]

POLICY_TERMS = [
    "政策",
    "标准",
    "法规",
    "合规",
    "环保",
    "双碳",
    "regulation",
    "policy",
    "standard",
    "compliance",
]

OFF_TOPIC_TERMS = [
    "体育",
    "马拉松",
    "足球",
    "篮球",
    "招聘",
    "房产",
    "地产",
    "按揭",
    "中签",
    "股价",
    "股票",
    "covered call",
    "helpline",
    "mortgage",
    "election",
    "marathon",
    "recall",
    "celebrity",
]

SOFT_REJECT_TERMS = [
    "market forecast",
    "forecast period",
    "market size",
    "cagr",
    "outlook",
    "estimated to reach",
    "融资",
    "募资",
    "股东",
    "盘前",
    "盘后",
    "券商",
    "研报",
    "价格走势",
    "行情",
    "观点评论",
]

PR_LIKE_TERMS = [
    "press release",
    "prnewswire",
    "business wire",
    "openpr",
    "estimated to reach",
    "market to exceed",
    "cagr",
    "market report",
    "forecast period",
    "预计将达到",
    "市场规模",
    "新闻稿",
]

BLOCKED_DOMAIN_PATTERNS = [
    "openpr.com",
    "bilibili.com",
    "cn.investing.com",
    "getspotnews.com",
    "caiwennews.com",
    "news.southcn.com",
    "coherentmarketinsights.com",
    "gminsights.com",
    "grandviewresearch.com",
    "precedenceresearch.com",
    "baike.baidu.com",
    "cn.dreamstime.com",
    "zhuanlan.zhihu.com",
    "sgpjbg.com",
    "zhihuiya.com",
    "globenewswire.com",
    "newswireservice.net",
    "facultyplus.com",
    "quantcha.com",
    "clutchpoints.com",
    "dailymail.co.uk",
    "prescottenews.com",
    "car-recalls.eu",
]

PR_WIRE_DOMAIN_PATTERNS = [
    "prnewswire.com",
    "prnasia.com",
    "businesswire.com",
]

TOP_INDUSTRY_MEDIA_PATTERNS = [
    "3dprint.com",
    "digitimes.com.tw",
    "adsalecprj.com",
    "plasticsnet.com.cn",
    "xdplas.com",
    "86pla.com",
    "hc360.com",
    "resourcemedia.eco",
    "downtoearth.org.in",
    "packaging-gateway.com",
]

GOVERNMENT_DOMAIN_PATTERNS = [
    "gov.cn",
    "miit.gov.cn",
    "ndrc.gov.cn",
    "mee.gov.cn",
]

STANDARDS_DOMAIN_PATTERNS = [
    "samr.gov.cn",
    "std.samr.gov.cn",
]

ACADEMIC_DOMAIN_PATTERNS = [
    "nature.com",
    "news.mit.edu",
    "sciencedirect.com",
    "springer.com",
    "acs.org",
]

SOURCE_TIER_SCORES = {
    "government": 1.0,
    "standards": 0.98,
    "top-industry-media": 0.86,
    "company-newsroom": 0.78,
    "academic-journal": 0.9,
    "pr-wire": 0.3,
    "unknown": 0.45,
}

SOURCE_TIER_THRESHOLDS = {
    "government": 0.5,
    "standards": 0.5,
    "top-industry-media": 0.54,
    "academic-journal": 0.56,
    "company-newsroom": 0.58,
    "unknown": 0.72,
    "pr-wire": 0.99,
}


class NativeReportPipeline:
    def __init__(self):
        self.brave = BraveSearchClient()
        self.firecrawl = FirecrawlClient()
        self.scraper = ScraperClient(jina_client=JinaReaderClient(), firecrawl_client=self.firecrawl)
        self.llm = ReportLLMService()

    async def run(
        self,
        session,
        shadow_mode: bool | None = None,
        report_date: date | None = None,
        mode: str = "publish",
    ) -> Report:
        settings_payload = get_report_settings(session)
        runtime = self._runtime_settings(settings_payload, shadow_mode)
        runtime["mode"] = mode
        target_date = report_date or now_local().date()
        window_end = now_local().replace(tzinfo=None)
        primary_window_start = window_end - timedelta(hours=PRIMARY_RETRIEVAL_WINDOW_HOURS)
        extended_window_start = window_end - timedelta(hours=EXTENDED_RETRIEVAL_WINDOW_HOURS)
        run = RetrievalRun(run_date=datetime.combine(target_date, datetime.min.time()), shadow_mode=runtime["shadow_mode"])
        session.add(run)
        session.flush()
        session.commit()

        provider_errors: list[str] = []
        fallbacks_triggered: list[str] = []
        stage_durations: dict[str, float] = {}
        planner_model: str | None = None
        scorer_model: str | None = None
        writer_model: str | None = None

        try:
            sources = list(session.scalars(select(Source).where(Source.enabled.is_(True)).order_by(desc(Source.priority))).all())
            feedback_stats = quality_feedback_domain_stats(session)

            planner_started = perf_counter()
            query_specs, priority_domains, planner_meta = await self._plan_queries(target_date, sources, runtime)
            stage_durations["planner_seconds"] = round(perf_counter() - planner_started, 3)
            planner_model = planner_meta.get("used_model")
            provider_errors.extend(planner_meta.get("provider_errors", []))
            if planner_meta.get("fallback_triggered"):
                fallbacks_triggered.append("planner")

            discovery_started = perf_counter()
            candidates = await self._discover_candidates(
                session,
                run,
                query_specs,
                sources,
                priority_domains,
                primary_window_start,
                extended_window_start,
                window_end,
            )
            stage_durations["discovery_seconds"] = round(perf_counter() - discovery_started, 3)

            deduped_candidates = self._deduplicate_candidates(candidates)
            run.query_count = len(query_specs)
            run.candidate_count = len(deduped_candidates)
            session.flush()
            session.commit()

            extraction_started = perf_counter()
            articles = await self._extract_articles(
                session,
                run,
                deduped_candidates,
                sources,
                target_date,
                primary_window_start,
                extended_window_start,
                window_end,
                runtime,
            )
            stage_durations["extraction_seconds"] = round(perf_counter() - extraction_started, 3)
            run.extracted_count = len(articles)
            self._build_clusters(session, run.id, articles)
            session.flush()
            session.commit()

            scoring_started = perf_counter()
            scored_articles, scorer_meta = await self._score_articles(
                session,
                run,
                articles,
                sources,
                target_date,
                runtime,
                feedback_stats,
            )
            stage_durations["scorer_seconds"] = round(perf_counter() - scoring_started, 3)
            scorer_model = scorer_meta.get("used_model")
            provider_errors.extend(scorer_meta.get("provider_errors", []))
            if scorer_meta.get("fallback_triggered"):
                fallbacks_triggered.append("scorer")

            writer_started = perf_counter()
            status, title, markdown, summary, report_items, writer_meta = await self._build_report_content(
                session,
                run,
                scored_articles,
                target_date,
                runtime,
            )
            stage_durations["writer_seconds"] = round(perf_counter() - writer_started, 3)
            writer_model = writer_meta.get("used_model")
            provider_errors.extend(writer_meta.get("provider_errors", []))
            if writer_meta.get("fallback_triggered"):
                fallbacks_triggered.append("writer")

            rejection_counts = self._rejection_counts(session, run.id)
            query_error_count = self._query_error_count(session, run.id)
            quality_gate_counts = self._quality_gate_counts(rejection_counts)
            excluded_domains = self._excluded_domains(session, run.id)
            domain_penalties = self._domain_penalties(session, run.id, feedback_stats)
            duplicate_ratio = self._duplicate_ratio(session, run.id)
            section_candidate_counts = self._section_candidate_counts(session, run.id)
            section_selected_counts = self._section_selected_counts(report_items)
            source_rule_rejections = self._source_rule_rejections(session, run.id)
            high_tier_rejections = self._high_tier_rejections(session, run.id)
            off_topic_rejections = rejection_counts.get("off_topic_candidate", 0) + rejection_counts.get("off_topic_content", 0)
            pr_like_rejections = rejection_counts.get("pr_like_candidate", 0) + rejection_counts.get("pr_like_content", 0)
            feedback_hits = self._feedback_hits(session, run.id, feedback_stats)
            section_coverage = distinct_sections(item["section"] for item in report_items)
            high_confidence_count = sum(1 for article in scored_articles if self._is_high_confidence(article))
            window_bucket_counts = self._window_bucket_counts(articles)
            extended_window_selected = sum(1 for item in report_items if item.get("window_bucket") == EXTENDED_WINDOW_BUCKET)
            metadata_fallback_count = sum(1 for article in articles if article.extraction_status == "search_fallback")
            policy_candidate_count = section_candidate_counts.get("policy", 0)
            policy_selected_count = sum(1 for item in report_items if item["section"] == "policy")
            per_domain_selected = self._per_domain_selected(report_items)
            top_policy_misses = self._top_policy_misses(rejection_counts, section_candidate_counts, policy_selected_count)
            operational_rejections = {
                reason: count
                for reason, count in rejection_counts.items()
                if reason.startswith("scrape_error") or reason in {"domain_circuit_breaker"}
            }
            if status in {"complete", "partial"} and (fallbacks_triggered or provider_errors or operational_rejections or query_error_count):
                status = "degraded"

            report = Report(
                report_date=target_date,
                status=status,
                title=title,
                markdown_content=markdown,
                summary=self._compose_report_summary(report_items, len(articles), rejection_counts, base_summary=summary),
                pipeline_version=settings.pipeline_version,
                retrieval_run_id=run.id,
                error_message=None if status != "failed" else summary,
            )
            session.add(report)
            session.flush()

            for payload in report_items:
                session.add(ReportItem(report_id=report.id, **payload))

            run.status = status
            run.finished_at = datetime.now(UTC)
            run.debug_payload = {
                "planner_model": planner_model,
                "scorer_model": scorer_model,
                "writer_model": writer_model,
                "stage_durations": stage_durations,
                "provider_errors": provider_errors,
                "rejection_counts": rejection_counts,
                "quality_gate_counts": quality_gate_counts,
                "domain_penalties": domain_penalties,
                "excluded_domains": excluded_domains,
                "duplicate_ratio": duplicate_ratio,
                "section_candidate_counts": section_candidate_counts,
                "section_selected_counts": section_selected_counts,
                "source_rule_rejections": source_rule_rejections,
                "high_tier_rejections": high_tier_rejections,
                "blocked_domains_hit": excluded_domains,
                "off_topic_rejections": off_topic_rejections,
                "pr_like_rejections": pr_like_rejections,
                "window_bucket_counts": window_bucket_counts,
                "extended_window_selected": extended_window_selected,
                "metadata_fallback_count": metadata_fallback_count,
                "policy_candidate_count": policy_candidate_count,
                "policy_selected_count": policy_selected_count,
                "per_domain_selected": per_domain_selected,
                "top_policy_misses": top_policy_misses,
                "feedback_hits": feedback_hits,
                "selected_count": len(report_items),
                "high_confidence_count": high_confidence_count,
                "section_coverage": section_coverage,
                "query_error_count": query_error_count,
                "fallbacks_triggered": fallbacks_triggered,
                "sources_enabled": len(sources),
                "brave_enabled": self.brave.enabled,
                "firecrawl_enabled": self.firecrawl.enabled,
            }
            session.flush()
            session.commit()
            return report
        except Exception as exc:
            session.rollback()
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = datetime.now(UTC)
            run.debug_payload = {
                "planner_model": planner_model,
                "scorer_model": scorer_model,
                "writer_model": writer_model,
                "stage_durations": stage_durations,
                "provider_errors": provider_errors + [str(exc)],
                "fallbacks_triggered": fallbacks_triggered,
            }
            session.add(run)
            session.commit()
            raise

    def _runtime_settings(self, payload: dict[str, Any], shadow_mode: bool | None) -> dict[str, Any]:
        return {
            "shadow_mode": settings.shadow_mode if shadow_mode is None else shadow_mode,
            "max_extractions_per_run": int(payload.get("max_extractions_per_run", settings.max_extractions_per_run)) if payload else settings.max_extractions_per_run,
            "scrape_timeout_seconds": int(payload.get("scrape_timeout_seconds", settings.scrape_timeout_seconds)) if payload else settings.scrape_timeout_seconds,
            "scrape_concurrency": max(1, int(payload.get("scrape_concurrency", settings.scrape_concurrency))) if payload else settings.scrape_concurrency,
            "report_primary_model": payload.get("report_primary_model", settings.report_primary_model) if payload else settings.report_primary_model,
            "report_fallback_model": payload.get("report_fallback_model", settings.report_fallback_model) if payload else settings.report_fallback_model,
            "domain_failure_threshold": settings.domain_failure_threshold,
            "primary_window_hours": PRIMARY_RETRIEVAL_WINDOW_HOURS,
            "extended_window_hours": EXTENDED_RETRIEVAL_WINDOW_HOURS,
        }

    async def _plan_queries(
        self,
        target_date: date,
        sources: list[Source],
        runtime: dict[str, Any],
    ) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
        source_payload = [
            {
                "domain": source.domain,
                "type": source.type,
                "priority": source.priority,
                "language": source.language,
                "tags": source.tags,
                "direct_source": source.use_direct_source,
                "source_tier": source.source_tier,
                "must_include_any": source.must_include_any or source.include_rules,
                "must_exclude_any": source.must_exclude_any or source.exclude_rules,
                "soft_signals": source.soft_signals,
            }
            for source in sources
        ]
        planner_output, meta = await self.llm.plan_queries(target_date, source_payload, SECTION_META, runtime)

        if planner_output is None:
            return self._default_query_specs(), [], meta

        query_specs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        counts_by_section: dict[str, int] = defaultdict(int)
        for row in planner_output.queries:
            section = row.section if row.section in SECTION_META else None
            language = row.language if row.language in {"zh", "en"} else None
            query = row.query.strip() if row.query else ""
            if not section or not language or len(query) < 3:
                continue
            if counts_by_section[section] >= SECTION_QUERY_LIMITS.get(section, 0):
                continue
            key = (section, query)
            if key in seen:
                continue
            seen.add(key)
            counts_by_section[section] += 1
            query_specs.append(
                {
                    "section": section,
                    "language": language,
                    "search_lang": settings.brave_search_lang if language == "zh" else settings.brave_fallback_lang,
                    "query": query,
                    "rationale": row.rationale[:240],
                }
            )

        static_queries_added = 0
        for section, meta_item in SECTION_META.items():
            for language, query in meta_item["queries"]:
                if counts_by_section[section] >= SECTION_QUERY_LIMITS.get(section, 0):
                    break
                key = (section, query)
                if key in seen:
                    continue
                seen.add(key)
                query_specs.append(
                    {
                        "section": section,
                        "language": language,
                        "search_lang": settings.brave_search_lang if language == "zh" else settings.brave_fallback_lang,
                        "query": query,
                        "rationale": f"Static slot query for {meta_item['report_name']} coverage.",
                    }
                )
                counts_by_section[section] += 1
                static_queries_added += 1

        if static_queries_added:
            meta = {**meta, "static_queries_added": static_queries_added}

        explicit_priority_domains = [domain for domain in planner_output.priority_domains if domain in {source.domain for source in sources}]
        source_priority_domains = [source.domain for source in sources if source.priority >= 80 or source.use_direct_source]
        priority_domains = list(dict.fromkeys(explicit_priority_domains + source_priority_domains))
        return query_specs[:MAX_QUERIES_PER_RUN], priority_domains[:12], meta

    async def _discover_candidates(
        self,
        session,
        run: RetrievalRun,
        query_specs: list[dict[str, Any]],
        sources: list[Source],
        priority_domains: list[str],
        primary_window_start: datetime,
        extended_window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        source_lookup = {source.domain: source for source in sources}

        for spec in query_specs:
            query_row = RetrievalQuery(
                run_id=run.id,
                section=spec["section"],
                language=spec["language"],
                query_text=spec["query"],
                target_type="mixed",
                filters={"goggles": spec.get("goggles"), "rationale": spec.get("rationale")},
            )
            session.add(query_row)
            session.flush()

            provider_errors: list[str] = []
            provider_counts: dict[str, int] = {}
            results: list[dict[str, Any]] = []
            try:
                if self.brave.enabled:
                    brave_results = await self.brave.search_all(spec["query"], spec["search_lang"], goggles=spec.get("goggles"))
                    provider_counts["brave"] = len(brave_results)
                    results.extend(brave_results)
            except Exception as exc:
                provider_errors.append(f"brave:{exc}")

            if self.firecrawl.enabled and len(results) < 2:
                try:
                    firecrawl_results = await self.firecrawl.search(spec["query"], limit=4)
                    provider_counts["firecrawl"] = len(firecrawl_results)
                    results.extend(firecrawl_results)
                except Exception as exc:
                    provider_errors.append(f"firecrawl:{exc}")

            if not results and provider_errors:
                query_row.response_status = "error"
                query_row.filters = {
                    **(query_row.filters or {}),
                    "error": "; ".join(provider_errors),
                    "provider_counts": provider_counts,
                }
                continue

            query_row.response_status = "ok" if results else "skipped"
            query_row.result_count = len(results)
            query_row.filters = {
                **(query_row.filters or {}),
                "provider_counts": provider_counts,
                "provider_errors": provider_errors,
            }
            for row in results:
                source = self._match_source_for_domain(row.get("domain") or extract_domain(row["url"]), source_lookup)
                candidate = self._candidate_from_search_result(row, spec["section"], spec["language"], source=source)
                persisted = self._persist_candidate(session, run.id, query_row.id, candidate)
                rejection_reason = self._prefilter_candidate(persisted, source, primary_window_start, extended_window_start, window_end)
                if rejection_reason:
                    self._reject_candidate(persisted, rejection_reason)
                    continue
                candidates.append(persisted)

        direct_candidates = await self._collect_direct_source_entries(
            sources,
            extended_window_start,
            window_end,
            priority_domains,
        )
        for candidate in direct_candidates:
            source = self._match_source_for_domain(candidate["domain"], source_lookup)
            persisted = self._persist_candidate(session, run.id, None, candidate)
            rejection_reason = self._prefilter_candidate(persisted, source, primary_window_start, extended_window_start, window_end)
            if rejection_reason:
                self._reject_candidate(persisted, rejection_reason)
                continue
            candidates.append(persisted)

        session.flush()
        session.commit()
        return candidates

    def _default_query_specs(self) -> list[dict[str, str]]:
        query_specs: list[dict[str, str]] = []
        counts_by_section: dict[str, int] = defaultdict(int)
        for section, meta in SECTION_META.items():
            for language, query in meta["queries"]:
                if counts_by_section[section] >= SECTION_QUERY_LIMITS.get(section, 0) or len(query_specs) >= MAX_QUERIES_PER_RUN:
                    break
                query_specs.append(
                    {
                        "section": section,
                        "language": language,
                        "search_lang": settings.brave_search_lang if language == "zh" else settings.brave_fallback_lang,
                        "query": query,
                        "rationale": "Static fallback query set.",
                    }
                )
                counts_by_section[section] += 1
        return query_specs[:MAX_QUERIES_PER_RUN]

    def _candidate_from_search_result(
        self,
        result: dict[str, Any],
        section: str,
        language: str,
        source: Source | None = None,
    ) -> dict[str, Any]:
        default_section = SOURCE_TYPE_TO_SECTION.get(source.type, section) if source else section
        classified_section = self._classify_section(result["title"], result.get("snippet"), source.type if source else section, default_section)
        domain = extract_domain(result["url"])
        return {
            "url": canonicalize_url(result["url"]),
            "title": result["title"].strip(),
            "domain": domain,
            "section": classified_section,
            "language": language,
            "source_type": source.type if source else classified_section,
            "source_name": source.name if source else (result.get("domain") or domain),
            "snippet": result.get("snippet"),
            "image_url": result.get("image_url"),
            "published_at": result.get("published_at"),
            "metadata": {
                **(result.get("metadata") or {}),
                "source_tier": self._resolve_source_tier(domain, source),
                "source_priority": source.priority if source else 10,
                "is_direct_source": False,
                "search_type": result.get("search_type") or "web",
            },
        }

    async def _collect_direct_source_entries(
        self,
        sources: list[Source],
        window_start: datetime,
        window_end: datetime,
        priority_domains: list[str],
    ) -> list[dict[str, Any]]:
        direct_candidates: list[dict[str, Any]] = []
        source_order = sorted(
            sources,
            key=lambda source: (
                0 if source.domain in priority_domains else 1,
                -source.priority,
            ),
        )
        for source in source_order:
            if not source.use_direct_source or not source.rss_or_listing_url:
                continue

            try:
                if source.crawl_mode == "rss":
                    feed_entries = await fetch_feed_entries(source.rss_or_listing_url, source.name, source.type)
                    for entry in feed_entries:
                        if not self._passes_source_rules(entry["title"], entry.get("snippet"), source):
                            continue
                        if entry.get("published_at") and not self._is_recent(entry["published_at"], window_start, window_end):
                            continue
                        direct_candidates.append(
                            {
                                "url": canonicalize_url(entry["url"]),
                                "title": entry["title"],
                                "domain": entry["domain"],
                                "section": SOURCE_TYPE_TO_SECTION.get(source.type, "industry"),
                                "language": source.language or infer_language(entry["title"], entry.get("snippet")),
                                "source_type": source.type,
                                "source_name": source.name,
                                "snippet": entry.get("snippet"),
                                "image_url": entry.get("image_url"),
                                "published_at": entry.get("published_at"),
                                "metadata": {
                                    **(entry.get("metadata") or {}),
                                    "source_tier": self._resolve_source_tier(source.domain, source),
                                    "source_priority": source.priority,
                                    "is_direct_source": True,
                                    "search_type": "direct",
                                },
                            }
                        )
                elif source.crawl_mode == "listing" and self.firecrawl.enabled:
                    links = await self.firecrawl.map(source.rss_or_listing_url)
                    for link in links[:8]:
                        mapped = self._normalize_listing_map_entry(link, source.rss_or_listing_url)
                        if mapped is None:
                            continue
                        direct_candidates.append(
                            {
                                "url": canonicalize_url(mapped["url"]),
                                "title": mapped["title"],
                                "domain": extract_domain(mapped["url"]),
                                "section": SOURCE_TYPE_TO_SECTION.get(source.type, "industry"),
                                "language": source.language or "zh",
                                "source_type": source.type,
                                "source_name": source.name,
                                "snippet": mapped.get("snippet"),
                                "image_url": None,
                                "published_at": mapped.get("published_at"),
                                "metadata": {
                                    "listing_url": source.rss_or_listing_url,
                                    "source_tier": self._resolve_source_tier(source.domain, source),
                                    "source_priority": source.priority,
                                    "is_direct_source": True,
                                    "search_type": "direct",
                                },
                            }
                        )
            except Exception:
                continue
        return direct_candidates

    def _normalize_listing_map_entry(self, link: Any, listing_url: str) -> dict[str, Any] | None:
        if isinstance(link, str):
            url = link
            title = link
            snippet = None
            published_at = None
        elif isinstance(link, dict):
            url = str(link.get("url") or link.get("href") or "").strip()
            title = str(link.get("title") or link.get("text") or url).strip()
            snippet = str(link.get("description") or link.get("snippet") or "").strip() or None
            published_at = parse_datetime(
                link.get("published_at")
                or link.get("publishedTime")
                or link.get("published")
                or link.get("date")
            )
        else:
            return None

        if not url:
            return None
        if not url.startswith("http"):
            url = urljoin(listing_url, url)
        if not url.startswith("http"):
            return None
        if self._is_listing_like_url(url, title):
            return None
        return {
            "url": url,
            "title": title or url,
            "snippet": snippet,
            "published_at": published_at,
        }

    def _is_listing_like_url(self, url: str, title: str | None = None) -> bool:
        lowered_url = url.lower()
        lowered_title = (title or "").lower()
        if any(token in lowered_url for token in ["/page/", "sitemap", ".xml", "/tag/", "/category/"]):
            return True
        if any(token in lowered_title for token in ["archives", "archive", "样本", "sample"]):
            return True
        return False

    def _deduplicate_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked_candidates = sorted(candidates, key=self._candidate_rank_key, reverse=True)
        seen_urls: dict[str, dict[str, Any]] = {}
        seen_titles: set[str] = set()
        domain_counts: dict[str, int] = defaultdict(int)
        for candidate in ranked_candidates:
            url = canonicalize_url(candidate["url"])
            title_key = normalize_title(candidate["title"])
            if url in seen_urls or title_key in seen_titles:
                row = candidate.get("candidate_row")
                if row is not None:
                    row.status = "rejected"
                    row.rejection_reason = "duplicate_url_or_title"
                continue
            if domain_counts[candidate["domain"]] >= DOMAIN_CANDIDATE_CAP:
                row = candidate.get("candidate_row")
                if row is not None:
                    row.status = "rejected"
                    row.rejection_reason = "domain_candidate_cap"
                continue
            seen_urls[url] = candidate
            seen_titles.add(title_key)
            domain_counts[candidate["domain"]] += 1
            row = candidate.get("candidate_row")
            if row is not None:
                row.status = "selected"
        return list(seen_urls.values())

    async def _extract_articles(
        self,
        session,
        run: RetrievalRun,
        candidates: list[dict[str, Any]],
        sources: list[Source],
        target_date: date,
        primary_window_start: datetime,
        extended_window_start: datetime,
        window_end: datetime,
        runtime: dict[str, Any],
    ) -> list[Article]:
        source_map = {source.domain: source for source in sources}
        prioritized = sorted(
            candidates,
            key=lambda item: (
                -(self._source_priority_for_candidate(item["domain"], source_map)),
                item["published_at"] or datetime.min,
            ),
            reverse=True,
        )

        selected: list[dict[str, Any]] = []
        extraction_domain_counts: dict[str, int] = defaultdict(int)
        for candidate in prioritized:
            if extraction_domain_counts[candidate["domain"]] >= PER_DOMAIN_EXTRACTION_CAP:
                row = candidate.get("candidate_row")
                if row is not None and row.status == "selected":
                    row.status = "rejected"
                    row.rejection_reason = "domain_extraction_cap"
                continue
            selected.append(candidate)
            extraction_domain_counts[candidate["domain"]] += 1
            if len(selected) >= runtime["max_extractions_per_run"]:
                break

        failure_counts: dict[str, int] = defaultdict(int)
        articles: list[Article] = []
        batch: list[dict[str, Any]] = []

        for candidate in selected:
            source = self._match_source_for_domain(candidate["domain"], source_map)
            if failure_counts[candidate["domain"]] >= runtime["domain_failure_threshold"]:
                self._reject_candidate(candidate, "domain_circuit_breaker")
                continue
            if source and not self._passes_source_rules(candidate["title"], candidate.get("snippet"), source):
                self._reject_candidate(candidate, "source_rules")
                continue

            batch.append(candidate)
            if len(batch) >= runtime["scrape_concurrency"]:
                articles.extend(
                    await self._process_extraction_batch(
                        session,
                        run,
                        batch,
                        source_map,
                        target_date,
                        primary_window_start,
                        extended_window_start,
                        window_end,
                        runtime,
                        failure_counts,
                    )
                )
                batch = []

        if batch:
            articles.extend(
                await self._process_extraction_batch(
                    session,
                    run,
                    batch,
                    source_map,
                    target_date,
                    primary_window_start,
                    extended_window_start,
                    window_end,
                    runtime,
                    failure_counts,
                )
            )

        return articles

    async def _process_extraction_batch(
        self,
        session,
        run: RetrievalRun,
        batch: list[dict[str, Any]],
        source_map: dict[str, Source],
        target_date: date,
        primary_window_start: datetime,
        extended_window_start: datetime,
        window_end: datetime,
        runtime: dict[str, Any],
        failure_counts: dict[str, int],
    ) -> list[Article]:
        results = await asyncio.gather(
            *[
                self.scraper.scrape(candidate["url"], timeout_seconds=runtime["scrape_timeout_seconds"])
                for candidate in batch
            ],
            return_exceptions=True,
        )

        articles: list[Article] = []
        for candidate, result in zip(batch, results, strict=False):
            source = self._match_source_for_domain(candidate["domain"], source_map)
            resolved_source_type = source.type if source else candidate["source_type"]
            if isinstance(result, Exception):
                failure_counts[candidate["domain"]] += 1
                fallback_article = self._build_search_fallback_article(
                    candidate,
                    source,
                    target_date,
                    primary_window_start,
                    extended_window_start,
                    window_end,
                    fallback_reason=f"scrape_error:{type(result).__name__}",
                )
                if fallback_article is None:
                    self._reject_candidate(candidate, f"scrape_error:{type(result).__name__}")
                    continue
                session.add(fallback_article)
                session.flush()
                row = candidate.get("candidate_row")
                if row is not None:
                    row.status = "extracted"
                articles.append(fallback_article)
                continue

            title = result.get("title") or candidate["title"]
            markdown = result.get("markdown") or candidate.get("snippet") or ""
            enriched_markdown = self._merge_candidate_context(candidate, markdown)
            source_rule_text = self._search_fallback_markdown(candidate)
            if self._looks_like_verification_wall(title, markdown):
                failure_counts[candidate["domain"]] += 1
                fallback_article = self._build_search_fallback_article(
                    candidate,
                    source,
                    target_date,
                    primary_window_start,
                    extended_window_start,
                    window_end,
                    fallback_reason="blocked_by_site",
                )
                if fallback_article is None:
                    self._reject_candidate(candidate, "blocked_by_site")
                    continue
                session.add(fallback_article)
                session.flush()
                row = candidate.get("candidate_row")
                if row is not None:
                    row.status = "extracted"
                articles.append(fallback_article)
                continue
            published_at = candidate.get("published_at") or result.get("published_at")
            if published_at is None:
                source_tier = self._resolve_source_tier(candidate["domain"], source)
                if source_tier in ALLOW_MISSING_PUBLISHED_AT_TIERS:
                    extracted_date = self._extract_policy_date(markdown, candidate["domain"])
                    if extracted_date:
                        published_at = extracted_date
                if published_at is None:
                    self._reject_candidate(candidate, "missing_published_at")
                    continue
            window_bucket = self._resolve_window_bucket(
                published_at,
                self._resolve_source_tier(candidate["domain"], source),
                primary_window_start,
                extended_window_start,
                window_end,
            )
            if published_at and window_bucket is None:
                self._reject_candidate(candidate, "outside_window")
                continue
            if source and not self._passes_source_rules(title, source_rule_text, source):
                self._reject_candidate(candidate, "source_rules_after_extract")
                continue
            section = self._classify_section(
                title,
                enriched_markdown,
                resolved_source_type,
                candidate["section"],
            )
            if not self._passes_content_gate(title, enriched_markdown, source, section):
                self._reject_candidate(candidate, "off_topic_content")
                continue

            article = Article(
                run_id=run.id,
                url=candidate["url"],
                canonical_url=candidate["url"],
                title=title,
                domain=candidate["domain"],
                source_type=resolved_source_type,
                section=section,
                language=infer_language(candidate.get("title"), enriched_markdown, candidate.get("snippet")),
                country=source.country if source else None,
                source_name=source.name if source else candidate["source_name"],
                published_at=published_at,
                image_url=(result.get("image_url") if (source.allow_images if source else True) else None) or candidate.get("image_url"),
                summary=summarize_markdown(enriched_markdown, candidate.get("snippet")),
                snippet=candidate.get("snippet"),
                raw_markdown=markdown,
                raw_html=result.get("html"),
                extraction_status=result.get("status", "success"),
                metadata_json={
                    **(candidate.get("metadata") or {}),
                    **(result.get("metadata") or {}),
                    "candidate_id": candidate.get("candidate_row").id if candidate.get("candidate_row") else None,
                    "source_tier": self._resolve_source_tier(candidate["domain"], source),
                    "window_bucket": window_bucket or candidate.get("metadata", {}).get("window_bucket") or PRIMARY_WINDOW_BUCKET,
                },
            )
            self._score_article_heuristic(article, target_date, source.priority if source else 40, self._resolve_source_tier(candidate["domain"], source))
            session.add(article)
            session.flush()
            row = candidate.get("candidate_row")
            if row is not None:
                row.status = "extracted"
            articles.append(article)

        session.flush()
        session.commit()
        return articles

    def _build_search_fallback_article(
        self,
        candidate: dict[str, Any],
        source: Source | None,
        target_date: date,
        primary_window_start: datetime,
        extended_window_start: datetime,
        window_end: datetime,
        fallback_reason: str,
    ) -> Article | None:
        source_tier = self._resolve_source_tier(candidate["domain"], source)
        if source_tier not in {"government", "standards", "top-industry-media", "academic-journal"}:
            return None
        published_at = candidate.get("published_at")
        window_bucket = self._resolve_window_bucket(
            published_at,
            source_tier,
            primary_window_start,
            extended_window_start,
            window_end,
        )
        if published_at is None or window_bucket is None:
            return None
        metadata = candidate.get("metadata") or {}
        if str(metadata.get("search_type") or "") == "images":
            return None

        fallback_markdown = self._search_fallback_markdown(candidate)
        if len(fallback_markdown.strip()) < 80:
            return None

        title = candidate["title"]
        resolved_source_type = source.type if source else candidate["source_type"]
        section = self._classify_section(title, fallback_markdown, resolved_source_type, candidate["section"])
        if source and not self._passes_source_rules(title, fallback_markdown, source):
            return None
        if not self._passes_content_gate(title, fallback_markdown, source, section):
            return None

        article = Article(
            run_id=candidate["candidate_row"].run_id,
            url=candidate["url"],
            canonical_url=candidate["url"],
            title=title,
            domain=candidate["domain"],
            source_type=resolved_source_type,
            section=section,
            language=infer_language(candidate.get("title"), fallback_markdown, candidate.get("snippet")),
            country=source.country if source else None,
            source_name=source.name if source else candidate["source_name"],
            published_at=published_at,
            image_url=(candidate.get("image_url") if (source.allow_images if source else True) else None),
            summary=summarize_markdown(fallback_markdown, candidate.get("snippet")),
            snippet=candidate.get("snippet"),
            raw_markdown=fallback_markdown,
            raw_html=None,
            extraction_status="search_fallback",
            metadata_json={
                **metadata,
                "candidate_id": candidate.get("candidate_row").id if candidate.get("candidate_row") else None,
                "source_tier": source_tier,
                "extraction_fallback": fallback_reason,
                "window_bucket": window_bucket,
            },
        )
        self._score_article_heuristic(article, target_date, source.priority if source else 40, source_tier)
        return article

    def _extract_policy_date(self, markdown: str, domain: str) -> datetime | None:
        """Extract publication date from policy/government page markdown content.

        Tries multiple Chinese date patterns commonly found in government releases.
        Returns datetime if found, None otherwise.
        """
        policy_domains = ("gov.cn", "miit.gov.cn", "samr.gov.cn", "ndrc.gov.cn", "mee.gov.cn",
                         "mofcom.gov.cn", "ccpit.org", "cnaec.org.cn", "cbmie.org")
        if not any(domain.endswith(p) for p in policy_domains) and "government" not in domain:
            return None

        text = markdown[:8000]
        date_patterns = [
            (r'(\d{4})年(\d{1,2})月(\d{1,2})日', lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r'发布[于在](\d{4})年(\d{1,2})月', lambda m: datetime(int(m[0]), int(m[1]), 1)),
            (r'发布日期[：:]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r'发文日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日', lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
            (r'日期[：:]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', lambda m: datetime(int(m[0]), int(m[1]), int(m[2]))),
        ]

        for pattern, parser in date_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return parser(match.groups())
                except (ValueError, OverflowError):
                    continue
        return None

    def _search_fallback_markdown(self, candidate: dict[str, Any]) -> str:
        return self._merge_candidate_context(candidate, candidate.get("snippet") or "")

    def _merge_candidate_context(self, candidate: dict[str, Any], base_text: str | None) -> str:
        metadata = candidate.get("metadata") or {}
        chunks: list[str] = []
        snippet = (base_text or "").strip()
        if snippet:
            chunks.append(snippet)
        title = (candidate.get("title") or "").strip()
        if title and title not in chunks:
            chunks.append(title)
        candidate_snippet = (candidate.get("snippet") or "").strip()
        if candidate_snippet and candidate_snippet not in chunks:
            chunks.append(candidate_snippet)
        for text in (metadata.get("extra_snippets") or [])[:3]:
            line = str(text).strip()
            if line and line not in chunks:
                chunks.append(line)
        description = str(metadata.get("description") or "").strip()
        if description and description not in chunks:
            chunks.append(description)
        return "\n\n".join(chunks)

    async def _score_articles(
        self,
        session,
        run: RetrievalRun,
        articles: list[Article],
        sources: list[Source],
        target_date: date,
        runtime: dict[str, Any],
        feedback_stats: dict[str, dict[str, int]] | None = None,
    ) -> tuple[list[Article], dict[str, Any]]:
        if not articles:
            return [], {"used_model": None, "provider_errors": [], "fallback_triggered": False}

        source_priority = {source.domain: source.priority for source in sources}
        source_lookup = {source.domain: source for source in sources}
        payload_articles = []
        for article in articles:
            matched_source = self._match_source_for_domain(article.domain, source_lookup)
            self._score_article_heuristic(
                article,
                target_date,
                source_priority.get(matched_source.domain if matched_source else article.domain, 40),
                self._resolve_source_tier(article.domain, matched_source, article),
                feedback_stats.get(article.domain, {}) if feedback_stats else None,
            )
            payload_articles.append(
                {
                    "article_id": article.id,
                    "title": article.title,
                    "summary": article.summary,
                    "domain": article.domain,
                    "source_name": article.source_name,
                    "section": article.section,
                    "published_at": article.published_at.isoformat() if article.published_at else None,
                    "image_present": bool(article.image_url),
                    "heuristic_combined_score": article.combined_score,
                    "source_tier": self._resolve_source_tier(article.domain, matched_source, article),
                }
            )

        llm_output, meta = await self.llm.score_articles(target_date, payload_articles, runtime)
        if llm_output is None:
            kept = self._select_articles_from_scores(articles)
            session.flush()
            session.commit()
            return kept, meta

        decisions = {row.article_id: row for row in llm_output.decisions}
        for article in articles:
            decision = decisions.get(article.id)
            if decision is None:
                continue
            article.section = decision.section if decision.section in SECTION_META else article.section
            article.freshness_score = self._clamp_score(decision.freshness_score, article.freshness_score)
            article.relevance_score = self._clamp_score(decision.relevance_score, article.relevance_score)
            article.source_trust_score = self._clamp_score(decision.source_trust_score, article.source_trust_score)
            article.research_value_score = self._clamp_score(decision.research_value_score, article.research_value_score)
            article.novelty_score = self._clamp_score(decision.novelty_score, article.novelty_score)
            article.combined_score = self._clamp_score(decision.combined_score, article.combined_score)
            article.metadata_json = {
                **(article.metadata_json or {}),
                "scoring_rationale": decision.rationale,
                "research_signal": decision.research_signal or self._research_signal(article),
            }

        kept = self._select_articles_from_scores(articles)
        if not kept:
            meta = {**meta, "fallback_triggered": True}

        session.flush()
        session.commit()
        return sorted(kept, key=lambda item: item.combined_score, reverse=True), meta

    def _score_article_heuristic(
        self,
        article: Article,
        target_date: date,
        source_priority: int,
        source_tier: str = "unknown",
        feedback_counts: dict[str, int] | None = None,
    ) -> None:
        freshness = 0.4
        if article.published_at:
            age = abs((datetime.combine(target_date, datetime.min.time()) - article.published_at.replace(tzinfo=None)).total_seconds()) / 3600
            freshness = max(0.1, 1.0 - min(age, 48) / 48)

        content = f"{article.title} {article.summary or ''}".lower()
        positive_hits = sum(1 for keyword in GLOBAL_TOPIC_TERMS if keyword.lower() in content)
        section_hits = sum(1 for keyword in {
            "academic": ACADEMIC_TERMS,
            "industry": INDUSTRY_TERMS,
            "policy": POLICY_TERMS,
        }.get(article.section, []) if keyword.lower() in content)
        negative_hits = sum(1 for keyword in OFF_TOPIC_TERMS if keyword.lower() in content)
        soft_reject_hits = sum(1 for keyword in SOFT_REJECT_TERMS if keyword.lower() in content)
        pr_hits = sum(1 for keyword in PR_LIKE_TERMS if keyword.lower() in content)

        relevance = min(1.0, 0.18 + positive_hits * 0.12 + section_hits * 0.11)
        relevance = max(0.0, relevance - negative_hits * 0.28 - pr_hits * 0.2 - soft_reject_hits * 0.1)

        source_trust = min(
            1.0,
            max(
                0.15,
                SOURCE_TIER_SCORES.get(source_tier, SOURCE_TIER_SCORES["unknown"]) * 0.75 + (source_priority / 100) * 0.25,
            ),
        )
        feedback_penalty = self._feedback_penalty_score(feedback_counts)
        source_trust = max(0.1, source_trust - feedback_penalty)

        research_value = 0.35
        if article.section == "academic":
            research_value = 0.82 if positive_hits > 0 else 0.45
        elif article.section == "policy":
            research_value = 0.72 if section_hits > 0 else 0.42
        elif any(term in content for term in ["扩产", "投产", "量产", "注塑机", "挤出机", "产线", "factory", "capacity"]):
            research_value = 0.74
        elif any(term in content for term in ["机理", "实验", "breakthrough", "mechanism"]):
            research_value = 0.78

        novelty = 0.62 if article.image_url else 0.48
        novelty = max(0.0, novelty - pr_hits * 0.1)

        article.freshness_score = round(freshness, 4)
        article.relevance_score = round(relevance, 4)
        article.source_trust_score = round(source_trust, 4)
        article.research_value_score = round(research_value, 4)
        article.novelty_score = round(novelty, 4)
        article.combined_score = round(
            freshness * 0.18 + relevance * 0.36 + source_trust * 0.24 + research_value * 0.18 + novelty * 0.04,
            4,
        )
        article.metadata_json = {
            **(article.metadata_json or {}),
            "source_tier": source_tier,
            "positive_hits": positive_hits,
            "section_hits": section_hits,
            "negative_hits": negative_hits,
            "soft_reject_hits": soft_reject_hits,
            "pr_hits": pr_hits,
            "feedback_penalty": round(feedback_penalty, 4),
            "feedback_counts": feedback_counts or {},
        }

    def _select_articles_from_scores(self, articles: list[Article]) -> list[Article]:
        eligible = [article for article in sorted(articles, key=lambda item: item.combined_score, reverse=True) if self._passes_final_quality_gate(article)]
        if not eligible:
            return []

        primary_articles = [article for article in eligible if self._article_window_bucket(article) == PRIMARY_WINDOW_BUCKET]
        extended_articles = [
            article
            for article in eligible
            if self._article_window_bucket(article) == EXTENDED_WINDOW_BUCKET
            and self._resolve_source_tier(article.domain, article=article) in EXTENDED_WINDOW_TIERS
        ]

        selected: list[Article] = []
        used_ids: set[int] = set()
        seen_title_keys: set[str] = set()
        seen_domains: dict[str, int] = defaultdict(int)

        def try_append(article: Article) -> bool:
            title_key = normalize_title(article.title)
            if article.id in used_ids or title_key in seen_title_keys:
                return False
            if seen_domains[article.domain] >= PER_DOMAIN_SELECTED_CAP:
                return False
            selected.append(article)
            used_ids.add(article.id)
            seen_title_keys.add(title_key)
            seen_domains[article.domain] += 1
            return True

        grouped_primary: dict[str, list[Article]] = defaultdict(list)
        for article in primary_articles:
            grouped_primary[article.section].append(article)

        for section in ("policy", "industry", "academic"):
            for article in grouped_primary.get(section, []):
                if try_append(article):
                    break

        for article in primary_articles:
            if len(selected) >= MAX_REPORT_ITEMS:
                break
            try_append(article)

        if len(selected) < MIN_COMPLETE_ITEMS:
            extended_added = 0
            grouped_extended: dict[str, list[Article]] = defaultdict(list)
            for article in extended_articles:
                grouped_extended[article.section].append(article)

            for section in ("policy", "industry"):
                if extended_added >= MAX_EXTENDED_WINDOW_ITEMS or len(selected) >= MAX_REPORT_ITEMS:
                    break
                if any(item.section == section for item in selected):
                    continue
                for article in grouped_extended.get(section, []):
                    if try_append(article):
                        extended_added += 1
                        break

            for article in extended_articles:
                if extended_added >= MAX_EXTENDED_WINDOW_ITEMS or len(selected) >= MAX_REPORT_ITEMS or len(selected) >= MIN_COMPLETE_ITEMS:
                    break
                if try_append(article):
                    extended_added += 1

        return sorted(selected[:MAX_REPORT_ITEMS], key=lambda item: item.combined_score, reverse=True)

    def _build_clusters(self, session, run_id: int, articles: list[Article]) -> None:
        grouped: dict[str, list[Article]] = defaultdict(list)
        for article in articles:
            cluster_key = make_cluster_key(article.title, article.domain)
            article.cluster_key = cluster_key
            grouped[cluster_key].append(article)

        for cluster_key, items in grouped.items():
            canonical = max(items, key=lambda item: item.combined_score)
            session.add(
                ArticleCluster(
                    run_id=run_id,
                    cluster_key=cluster_key,
                    canonical_article_id=canonical.id,
                    article_count=len(items),
                )
            )

    async def _build_report_content(
        self,
        session,
        run: RetrievalRun,
        articles: list[Article],
        target_date: date,
        runtime: dict[str, Any],
    ) -> tuple[str, str, str, str, list[dict[str, Any]], dict[str, Any]]:
        if not articles:
            summary = "未命中足够候选文章，请检查 Brave、Firecrawl、OpenRouter 配置和来源规则。"
            markdown = "\n".join(
                [
                    f"# {settings.report_title}（{target_date.isoformat()}）",
                    "",
                    "## ⚠️ 当前未生成正式日报",
                    summary,
                ]
            )
            return "failed", f"{settings.report_title}（{target_date.isoformat()}）", markdown, summary, [], {
                "used_model": None,
                "provider_errors": [],
                "fallback_triggered": False,
            }

        article_map = {article.id: article for article in articles}
        writer_payload = [
            {
                "article_id": article.id,
                "title": article.title,
                "section": article.section,
                "summary": article.summary or article.snippet or "暂无摘要",
                "research_signal": self._article_research_signal(article),
                "source_name": article.source_name or article.domain,
                "source_url": article.url,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "combined_score": article.combined_score,
            }
            for article in sorted(articles, key=lambda item: item.combined_score, reverse=True)[:MAX_REPORT_ITEMS]
        ]

        writer_output, meta = await self.llm.write_report(
            target_date,
            f"{settings.report_title}（{target_date.isoformat()}）",
            writer_payload,
            runtime,
        )
        if writer_output is None:
            status, title, markdown, summary, items = self._build_template_report_content(articles, target_date)
            return "degraded", title, markdown, summary, items, meta

        report_items = self._hydrate_writer_items(writer_output, article_map)
        if not report_items:
            meta = {**meta, "fallback_triggered": True}
            status, title, markdown, summary, items = self._build_template_report_content(articles, target_date)
            return "degraded", title, markdown, summary, items, meta

        report_items = report_items[:MAX_REPORT_ITEMS]
        status = self._status_for_report_items(report_items)
        return status, writer_output.title, writer_output.markdown_content, writer_output.summary, report_items, meta

    def _build_template_report_content(
        self,
        articles: list[Article],
        target_date: date,
    ) -> tuple[str, str, str, str, list[dict[str, Any]]]:
        grouped: dict[str, list[Article]] = defaultdict(list)
        for article in sorted(articles, key=lambda item: item.combined_score, reverse=True):
            grouped[article.section].append(article)

        title = f"{settings.report_title}（{target_date.isoformat()}）"
        lines = [f"# {title}", ""]
        report_items: list[dict[str, Any]] = []

        for section in ("academic", "industry", "policy"):
            items = grouped.get(section, [])[: min(settings.max_items_per_section, 2)]
            if not items:
                continue
            lines.append(SECTION_META[section]["heading"])
            for index, article in enumerate(items, start=1):
                if len(report_items) >= MAX_REPORT_ITEMS:
                    break
                signal = self._article_research_signal(article)
                published = article.published_at.strftime("%Y-%m-%d %H:%M") if article.published_at else "未知"
                lines.extend(
                    [
                        f"### {index}. {article.title}",
                        f"* **来源**：[{article.source_name or article.domain}]({article.url})",
                        f"* **时间**：{published}",
                        f"* **摘要**：{article.summary or article.snippet or '暂无摘要'}",
                        f"* **科研雷达**：{signal}",
                        "",
                    ]
                )
                report_items.append(
                    {
                        "article_id": article.id,
                        "section": section,
                        "rank": index,
                        "title": article.title,
                        "source_name": article.source_name or article.domain,
                        "source_url": article.url,
                        "published_at": article.published_at,
                        "summary": article.summary or article.snippet or "暂无摘要",
                        "research_signal": signal,
                        "image_url": article.image_url,
                        "window_bucket": self._article_window_bucket(article),
                        "citations": [{"label": article.source_name or article.domain, "url": article.url}],
                        "combined_score": article.combined_score,
                    }
                )
            if len(report_items) >= MAX_REPORT_ITEMS:
                break

        status = self._status_for_report_items(report_items)
        summary = self._compose_report_summary(report_items, len(articles), {})
        return status, title, "\n".join(lines).strip(), summary, report_items

    def _hydrate_writer_items(self, writer_output: WriterOutput, article_map: dict[int, Article]) -> list[dict[str, Any]]:
        report_items: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for row in sorted(writer_output.items, key=lambda item: item.rank):
            article = article_map.get(row.article_id)
            if article is None or row.article_id in seen_ids or not self._passes_final_quality_gate(article):
                return []
            seen_ids.add(row.article_id)
            report_items.append(
                {
                    "article_id": article.id,
                    "section": row.section if row.section in SECTION_META else article.section,
                    "rank": row.rank,
                    "title": article.title,
                    "source_name": article.source_name or article.domain,
                    "source_url": article.url,
                    "published_at": article.published_at,
                    "summary": row.summary or article.summary or article.snippet or "暂无摘要",
                    "research_signal": row.research_signal or self._article_research_signal(article),
                    "image_url": article.image_url,
                    "window_bucket": self._article_window_bucket(article),
                    "citations": [{"label": article.source_name or article.domain, "url": article.url}],
                    "combined_score": article.combined_score,
                }
            )
        return report_items

    def _article_research_signal(self, article: Article) -> str:
        metadata = article.metadata_json or {}
        return metadata.get("research_signal") or self._research_signal(article)

    def _research_signal(self, article: Article) -> str:
        content = f"{article.title} {article.summary or ''}"
        if article.section == "academic":
            return "关注其机理验证、工艺窗口和可放大性，评估是否具备向高分子加工场景迁移的潜力。"
        if article.section == "industry":
            if "3d" in content.lower() or "打印" in content:
                return "关注材料流变、成形窗口和设备适配关系，这通常会直接反馈到配方与加工研究。"
            return "关注其背后的设备升级、材料性能需求和量产约束，判断哪些方向值得提前布局。"
        return "关注政策、标准或终端需求变化对材料选择、回收体系和合规测试带来的研究牵引。"

    def _match_source_for_domain(self, domain: str, source_map: dict[str, Source]) -> Source | None:
        normalized = extract_domain(domain if domain.startswith("http") else f"https://{domain}")
        exact = source_map.get(normalized)
        if exact is not None:
            return exact
        for source_domain, source in sorted(source_map.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized == source_domain or normalized.endswith(f".{source_domain}"):
                return source
        return None

    def _source_priority_for_candidate(self, domain: str, source_map: dict[str, Source]) -> int:
        source = self._match_source_for_domain(domain, source_map)
        return source.priority if source else 10

    def _resolve_source_tier(self, domain: str, source: Source | None = None, article: Article | None = None) -> str:
        if article is not None:
            metadata = article.metadata_json or {}
            if metadata.get("source_tier"):
                return str(metadata["source_tier"])
        if source and source.source_tier:
            return source.source_tier
        normalized = extract_domain(domain if domain.startswith("http") else f"https://{domain}")
        if any(pattern in normalized for pattern in STANDARDS_DOMAIN_PATTERNS):
            return "standards"
        if any(pattern in normalized for pattern in GOVERNMENT_DOMAIN_PATTERNS):
            return "government"
        if any(pattern in normalized for pattern in TOP_INDUSTRY_MEDIA_PATTERNS):
            return "top-industry-media"
        if any(pattern in normalized for pattern in PR_WIRE_DOMAIN_PATTERNS):
            return "pr-wire"
        if any(pattern in normalized for pattern in ACADEMIC_DOMAIN_PATTERNS):
            return "academic-journal"
        return "unknown"

    def _resolve_window_bucket(
        self,
        published_at: datetime | None,
        source_tier: str,
        primary_window_start: datetime,
        extended_window_start: datetime,
        window_end: datetime,
    ) -> str | None:
        if published_at is None:
            return None
        published_local = published_at.replace(tzinfo=None)
        if self._is_recent(published_local, primary_window_start, window_end):
            return PRIMARY_WINDOW_BUCKET
        if source_tier in EXTENDED_WINDOW_TIERS and self._is_recent(published_local, extended_window_start, window_end):
            return EXTENDED_WINDOW_BUCKET
        return None

    def _article_window_bucket(self, article: Article) -> str:
        metadata = article.metadata_json or {}
        return str(metadata.get("window_bucket") or PRIMARY_WINDOW_BUCKET)

    def _prefilter_candidate(
        self,
        candidate: dict[str, Any],
        source: Source | None,
        primary_window_start: datetime | None = None,
        extended_window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> str | None:
        domain = candidate["domain"]
        content = f"{candidate['title']} {candidate.get('snippet') or ''}".lower()
        source_tier = self._resolve_source_tier(domain, source)
        metadata = candidate.get("metadata") or {}
        published_at = candidate.get("published_at")
        if any(pattern in domain for pattern in BLOCKED_DOMAIN_PATTERNS):
            return "blocked_domain"
        if published_at and primary_window_start and extended_window_start and window_end:
            window_bucket = self._resolve_window_bucket(
                published_at,
                source_tier,
                primary_window_start,
                extended_window_start,
                window_end,
            )
            if window_bucket is None:
                return "outside_window"
            metadata["window_bucket"] = window_bucket
            candidate["metadata"] = metadata
            row = candidate.get("candidate_row")
            if row is not None:
                row.metadata_json = metadata
        if (
            published_at is None
            and metadata.get("search_type") == "images"
        ):
            return "missing_published_at_candidate"
        if (
            published_at is None
            and not metadata.get("is_direct_source")
            and source_tier not in ALLOW_MISSING_PUBLISHED_AT_TIERS
        ):
            return "missing_published_at_candidate"
        if self._contains_any(content, OFF_TOPIC_TERMS):
            return "off_topic_candidate"
        if not self._passes_source_rules(candidate["title"], candidate.get("snippet"), source):
            return "source_rules"
        if not self._passes_topic_gate(candidate["title"], candidate.get("snippet"), source):
            return "off_topic_candidate"
        if self._is_pr_like(domain, content) and not self._passes_strong_relevance_signal(content):
            return "pr_like_candidate"
        return None

    def _passes_topic_gate(self, title: str, text: str | None, source: Source | None) -> bool:
        content = f"{title} {text or ''}".lower()
        if source:
            must_include = list(source.must_include_any or []) or list(source.include_rules or [])
            if must_include and not self._contains_any(content, must_include):
                if self._is_high_tier_source(source.source_tier) and self._passes_high_tier_exception(content, source.type):
                    return True
                return False
            return self._contains_any(content, GLOBAL_TOPIC_TERMS + list(source.soft_signals or []) + must_include)
        return self._contains_any(content, GLOBAL_TOPIC_TERMS)

    def _passes_content_gate(self, title: str, text: str | None, source: Source | None, section: str) -> bool:
        content = f"{title} {text or ''}".lower()
        if self._contains_any(content, OFF_TOPIC_TERMS):
            return False
        if not self._passes_topic_gate(title, text, source):
            return False
        section_terms = {
            "academic": ACADEMIC_TERMS,
            "industry": INDUSTRY_TERMS,
            "policy": POLICY_TERMS,
        }.get(section, [])
        return self._contains_any(content, GLOBAL_TOPIC_TERMS) and (
            self._contains_any(content, section_terms) or self._passes_strong_relevance_signal(content)
        )

    def _passes_final_quality_gate(self, article: Article) -> bool:
        content = f"{article.title} {article.summary or ''}".lower()
        source_tier = self._resolve_source_tier(article.domain, article=article)
        if article.published_at is None:
            return False
        if source_tier == "pr-wire":
            return False
        if article.combined_score < self._quality_threshold_for_tier(source_tier):
            return False
        if self._contains_any(content, OFF_TOPIC_TERMS):
            return False
        if self._contains_any(content, SOFT_REJECT_TERMS) and article.combined_score < max(
            HIGH_CONFIDENCE_SCORE_THRESHOLD,
            self._quality_threshold_for_tier(source_tier) + 0.08,
        ):
            return False
        if self._is_pr_like(article.domain, content):
            return False
        if source_tier == "unknown" and article.combined_score < HIGH_CONFIDENCE_SCORE_THRESHOLD:
            return False
        if not self._passes_content_gate(article.title, article.raw_markdown or article.summary, None, article.section):
            return False
        return True

    def _compose_report_summary(
        self,
        report_items: list[dict[str, Any]],
        candidate_article_count: int,
        rejection_counts: dict[str, int],
        base_summary: str | None = None,
    ) -> str:
        top_rejections = sorted(rejection_counts.items(), key=lambda item: item[1], reverse=True)[:3]
        rejection_text = "、".join(f"{reason}:{count}" for reason, count in top_rejections) if top_rejections else "无"
        coverage = distinct_sections(item["section"] for item in report_items)
        bits = [
            f"有效候选 {candidate_article_count} 条",
            f"高质量入选 {len(report_items)} 条",
            f"覆盖板块 {coverage} 个",
            f"主要拒绝原因 {rejection_text}",
        ]
        extended_count = sum(1 for item in report_items if item.get("window_bucket") == EXTENDED_WINDOW_BUCKET)
        if extended_count:
            bits.append(f"36h补位 {extended_count} 条")
        if base_summary:
            bits.insert(0, base_summary)
        return "；".join(bits)

    def _status_for_report_items(self, report_items: list[dict[str, Any]]) -> str:
        if not report_items:
            return "failed"
        section_count = distinct_sections(item["section"] for item in report_items)
        extended_used = any(item.get("window_bucket") == EXTENDED_WINDOW_BUCKET for item in report_items)
        verified_image_count = sum(1 for item in report_items if item.get("has_verified_image"))
        # complete: 3+ items + 2+ sections + no extended (images tracked but not blocking)
        if len(report_items) >= MIN_COMPLETE_ITEMS and section_count >= MIN_COMPLETE_SECTIONS and not extended_used:
            return "complete"
        # partial: any items that don't meet complete criteria
        return "partial"

    def _partial_gap_description(self, report_items: list[dict[str, Any]], image_gap_reason: str | None = None, policy_gap_reason: str | None = None) -> str | None:
        """Generate a human-readable description of what's missing for partial status."""
        if not report_items:
            return "无可发布内容"
        section_count = distinct_sections(item["section"] for item in report_items)
        verified_image_count = sum(1 for item in report_items if item.get("has_verified_image"))
        gaps: list[str] = []
        if len(report_items) < MIN_COMPLETE_ITEMS:
            gaps.append(f"条目不足({len(report_items)}/{MIN_COMPLETE_ITEMS})")
        if section_count < MIN_COMPLETE_SECTIONS:
            gaps.append(f"板块不足({section_count}/{MIN_COMPLETE_SECTIONS})")
        if verified_image_count < MIN_COMPLETE_IMAGES:
            gaps.append(f"图片不足({verified_image_count}/{MIN_COMPLETE_IMAGES})")
        if policy_gap_reason:
            gaps.append(f"政策内容: {policy_gap_reason}")
        if image_gap_reason:
            gaps.append(f"图片来源: {image_gap_reason}")
        return " | ".join(gaps) if gaps else None

    def _quality_gate_counts(self, rejection_counts: dict[str, IntProperty]) -> dict[str, int]:
        return {
            "blocked_domain": rejection_counts.get("blocked_domain", 0),
            "duplicate": rejection_counts.get("duplicate_url_or_title", 0),
            "domain_cap": rejection_counts.get("domain_candidate_cap", 0),
            "missing_published_at": rejection_counts.get("missing_published_at", 0) + rejection_counts.get("missing_published_at_candidate", 0),
            "off_topic": rejection_counts.get("off_topic_candidate", 0) + rejection_counts.get("off_topic_content", 0),
            "pr_like": rejection_counts.get("pr_like_candidate", 0) + rejection_counts.get("pr_like_content", 0),
            "source_rules": rejection_counts.get("source_rules", 0) + rejection_counts.get("source_rules_after_extract", 0),
            "outside_window": rejection_counts.get("outside_window", 0),
        }

    def _excluded_domains(self, session, run_id: int) -> list[str]:
        stmt = select(RetrievalCandidate.domain).where(
            RetrievalCandidate.run_id == run_id,
            RetrievalCandidate.rejection_reason.in_(["blocked_domain", "pr_like_candidate"]),
        )
        return sorted({domain for domain in session.scalars(stmt).all() if domain})

    def _domain_penalties(self, session, run_id: int, feedback_stats: dict[str, dict[str, int]] | None = None) -> dict[str, int]:
        penalties: dict[str, int] = defaultdict(int)
        stmt = select(RetrievalCandidate.domain, RetrievalCandidate.rejection_reason).where(RetrievalCandidate.run_id == run_id)
        for domain, reason in session.execute(stmt).all():
            if not domain or not reason:
                continue
            if reason in {"blocked_domain", "pr_like_candidate", "off_topic_candidate", "off_topic_content"}:
                penalties[domain] += 1
        for domain, counts in (feedback_stats or {}).items():
            penalty = self._feedback_penalty_score(counts)
            if penalty > 0:
                penalties[domain] += round(penalty * 10)
        return dict(sorted(penalties.items(), key=lambda item: item[1], reverse=True)[:8])

    def _feedback_hits(self, session, run_id: int, feedback_stats: dict[str, dict[str, int]] | None = None) -> int:
        if not feedback_stats:
            return 0
        domains = {
            domain
            for domain in session.scalars(select(RetrievalCandidate.domain).where(RetrievalCandidate.run_id == run_id)).all()
            if domain
        }
        return sum(1 for domain in domains if self._feedback_penalty_score(feedback_stats.get(domain, {})) > 0)

    def _duplicate_ratio(self, session, run_id: int) -> float:
        total = len(list(session.scalars(select(RetrievalCandidate.id).where(RetrievalCandidate.run_id == run_id)).all()))
        if total == 0:
            return 0.0
        duplicates = len(
            list(
                session.scalars(
                    select(RetrievalCandidate.id).where(
                        RetrievalCandidate.run_id == run_id,
                        RetrievalCandidate.rejection_reason == "duplicate_url_or_title",
                    )
                ).all()
            )
        )
        return round(duplicates / total, 4)

    def _section_candidate_counts(self, session, run_id: int) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        rows = session.execute(
            select(RetrievalCandidate.section, RetrievalCandidate.rejection_reason).where(RetrievalCandidate.run_id == run_id)
        ).all()
        for section, reason in rows:
            if not section or reason in {"duplicate_url_or_title", "domain_candidate_cap"}:
                continue
            counts[section] += 1
        return dict(counts)

    def _section_selected_counts(self, report_items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for row in report_items:
            counts[row["section"]] += 1
        return dict(counts)

    def _window_bucket_counts(self, articles: list[Article]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for article in articles:
            counts[self._article_window_bucket(article)] += 1
        return dict(counts)

    def _per_domain_selected(self, report_items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for item in report_items:
            counts[extract_domain(item["source_url"])] += 1
        return dict(counts)

    def _top_policy_misses(
        self,
        rejection_counts: dict[str, int],
        section_candidate_counts: dict[str, int],
        policy_selected_count: int,
    ) -> dict[str, int]:
        if section_candidate_counts.get("policy", 0) <= 0 or policy_selected_count > 0:
            return {}
        misses: dict[str, int] = {}
        for reason in (
            "outside_window",
            "missing_published_at_candidate",
            "source_rules",
            "source_rules_after_extract",
            "off_topic_candidate",
            "off_topic_content",
            "blocked_domain",
        ):
            if rejection_counts.get(reason):
                misses[reason] = int(rejection_counts[reason])
        return misses

    def _source_rule_rejections(self, session, run_id: int) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        rows = session.execute(
            select(RetrievalCandidate.domain, RetrievalCandidate.rejection_reason).where(
                RetrievalCandidate.run_id == run_id,
                RetrievalCandidate.rejection_reason.in_(["source_rules", "source_rules_after_extract"]),
            )
        ).all()
        for domain, _reason in rows:
            if domain:
                counts[domain] += 1
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8])

    def _high_tier_rejections(self, session, run_id: int) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        rows = session.execute(
            select(
                RetrievalCandidate.domain,
                RetrievalCandidate.rejection_reason,
                RetrievalCandidate.metadata_json,
            ).where(
                RetrievalCandidate.run_id == run_id,
                RetrievalCandidate.rejection_reason.in_(["source_rules", "source_rules_after_extract", "off_topic_content", "off_topic_candidate"]),
            )
        ).all()
        for domain, _reason, metadata in rows:
            source_tier = str((metadata or {}).get("source_tier") or "")
            if domain and self._is_high_tier_source(source_tier):
                counts[domain] += 1
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8])

    def _classify_section(self, title: str, snippet: str | None, source_type: str, default: str) -> str:
        content = f"{title} {snippet or ''}".lower()
        if source_type == "policy":
            return "policy"
        if source_type == "academic" and self._contains_any(content, GLOBAL_TOPIC_TERMS + ACADEMIC_TERMS):
            return "academic"
        if self._contains_any(content, POLICY_TERMS):
            return "policy"
        if self._contains_any(content, ACADEMIC_TERMS) and self._contains_any(content, GLOBAL_TOPIC_TERMS):
            return "academic"
        if self._contains_any(content, INDUSTRY_TERMS) and self._contains_any(content, GLOBAL_TOPIC_TERMS):
            return "industry"
        return SOURCE_TYPE_TO_SECTION.get(source_type, default)

    def _passes_source_rules(self, title: str, text: str | None, source: Source | None) -> bool:
        if source is None:
            return True
        content = f"{title} {text or ''}".lower()
        combined_excludes = list(source.must_exclude_any or []) + list(source.exclude_rules or [])
        combined_includes = list(source.must_include_any or []) + list(source.include_rules or [])
        if combined_excludes and self._contains_excluded_term(content, combined_excludes):
            return False
        if combined_includes:
            if self._contains_any(content, combined_includes):
                return True
            if self._is_high_tier_source(source.source_tier) and self._passes_high_tier_exception(content, source.type):
                return True
            return False
        if self._is_high_tier_source(source.source_tier) and self._passes_high_tier_exception(content, source.type):
            return True
        return self._contains_any(content, GLOBAL_TOPIC_TERMS + list(source.soft_signals or []))

    def _is_recent(self, published_at: datetime, window_start: datetime, window_end: datetime) -> bool:
        candidate = published_at.replace(tzinfo=None)
        return window_start <= candidate <= window_end

    def _persist_candidate(self, session, run_id: int, query_id: int | None, candidate: dict[str, Any]) -> dict[str, Any]:
        row = RetrievalCandidate(
            run_id=run_id,
            query_id=query_id,
            url=candidate["url"],
            title=candidate["title"],
            domain=candidate["domain"],
            section=candidate["section"],
            language=candidate["language"],
            source_type=candidate["source_type"],
            source_name=candidate.get("source_name"),
            status="discovered",
            rejection_reason=None,
            image_url=candidate.get("image_url"),
            published_at=candidate.get("published_at"),
            metadata_json=candidate.get("metadata") or {},
        )
        session.add(row)
        session.flush()
        candidate["candidate_row"] = row
        return candidate

    def _reject_candidate(self, candidate: dict[str, Any], reason: str) -> None:
        row = candidate.get("candidate_row")
        if row is not None:
            row.status = "rejected"
            row.rejection_reason = reason

    def _rejection_counts(self, session, run_id: int) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        stmt = select(RetrievalCandidate.rejection_reason).where(
            RetrievalCandidate.run_id == run_id,
            RetrievalCandidate.rejection_reason.is_not(None),
        )
        for reason in session.scalars(stmt).all():
            counts[reason] += 1
        return dict(counts)

    def _query_error_count(self, session, run_id: int) -> int:
        stmt = select(RetrievalQuery).where(
            RetrievalQuery.run_id == run_id,
            RetrievalQuery.response_status == "error",
        )
        return len(list(session.scalars(stmt).all()))

    def _clamp_score(self, value: float, fallback: float) -> float:
        try:
            return round(min(1.0, max(0.0, float(value))), 4)
        except (TypeError, ValueError):
            return fallback

    def _contains_any(self, content: str, keywords: list[str]) -> bool:
        return any(keyword.lower() in content for keyword in keywords if keyword)

    def _contains_excluded_term(self, content: str, keywords: list[str]) -> bool:
        for keyword in keywords:
            if not keyword:
                continue
            lowered = keyword.lower().strip()
            if not lowered:
                continue
            if lowered.isascii():
                pattern = r"(?<!\w)" + re.escape(lowered).replace(r"\ ", r"\s+") + r"(?!\w)"
                if re.search(pattern, content):
                    return True
                continue
            if lowered in content:
                return True
        return False

    def _is_pr_like(self, domain: str, content: str) -> bool:
        return any(pattern in domain for pattern in PR_WIRE_DOMAIN_PATTERNS) or self._contains_any(content, PR_LIKE_TERMS)

    def _passes_strong_relevance_signal(self, content: str) -> bool:
        return self._contains_any(content, GLOBAL_TOPIC_TERMS) and (
            self._contains_any(content, INDUSTRY_TERMS)
            or self._contains_any(content, ACADEMIC_TERMS)
            or self._contains_any(content, POLICY_TERMS)
        )

    def _passes_high_tier_exception(self, content: str, source_type: str) -> bool:
        if source_type == "policy":
            return self._contains_any(content, GLOBAL_TOPIC_TERMS + POLICY_TERMS)
        if source_type == "academic":
            return self._contains_any(content, GLOBAL_TOPIC_TERMS + ACADEMIC_TERMS)
        return self._passes_strong_relevance_signal(content)

    def _looks_like_verification_wall(self, title: str | None, content: str | None) -> bool:
        combined = f"{title or ''} {content or ''}".lower()
        return any(
            token in combined
            for token in [
                "正在验证",
                "安全验证",
                "verify you are human",
                "verification required",
                "captcha",
                "访问验证",
            ]
        )

    def _is_high_tier_source(self, source_tier: str) -> bool:
        return source_tier in {"government", "standards", "top-industry-media", "academic-journal"}

    def _candidate_rank_key(self, candidate: dict[str, Any]) -> tuple[int, int, float, datetime]:
        metadata = candidate.get("metadata") or {}
        source_tier = str(metadata.get("source_tier") or "unknown")
        source_priority = int(metadata.get("source_priority") or 10)
        search_type = str(metadata.get("search_type") or "web")
        published = candidate.get("published_at")
        published_dt = published.replace(tzinfo=None) if isinstance(published, datetime) else datetime.min
        search_rank = {"direct": 3, "news": 2, "web": 1, "images": 0}.get(search_type, 0)
        return (
            1 if metadata.get("is_direct_source") else 0,
            source_priority,
            search_rank,
            SOURCE_TIER_SCORES.get(source_tier, SOURCE_TIER_SCORES["unknown"]),
            published_dt,
        )

    def _quality_threshold_for_tier(self, source_tier: str) -> float:
        return SOURCE_TIER_THRESHOLDS.get(source_tier, QUALITY_SCORE_THRESHOLD)

    def _is_high_confidence(self, article: Article) -> bool:
        return article.combined_score >= max(
            HIGH_CONFIDENCE_SCORE_THRESHOLD,
            self._quality_threshold_for_tier(self._resolve_source_tier(article.domain, article=article)) + 0.08,
        )

    def _feedback_penalty_score(self, counts: dict[str, int] | None) -> float:
        if not counts:
            return 0.0
        bad = counts.get("bad_off_topic", 0) * 2 + counts.get("bad_pr_like", 0) * 2 + counts.get("bad_source", 0)
        good = counts.get("good", 0) + counts.get("keep_borderline", 0)
        return max(0.0, min(0.24, (bad - good) * 0.04))
