from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models import Report, ReportItem, RetrievalRun
from app.services.rss import fetch_feed_entries
from app.utils import canonicalize_url, now_local


DEFAULT_AI_FEED_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"
AI_RECENCY_HOURS = 72
MAX_AI_ITEMS = 24

ACADEMIC_TERMS = [
    "research",
    "paper",
    "benchmark",
    "dataset",
    "robotics",
    "模型",
    "研究",
    "论文",
    "基准",
    "机器人",
]
POLICY_TERMS = [
    "policy",
    "law",
    "safety",
    "compliance",
    "copyright",
    "governance",
    "监管",
    "政策",
    "安全",
    "合规",
    "版权",
]
SOCIAL_DOMAINS = {"x.com", "twitter.com", "mp.weixin.qq.com"}
OFFICIAL_DOMAIN_PATTERNS = [
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "googleblog.com",
    "blog.google",
    "microsoft.ai",
    "techcommunity.microsoft.com",
    "developer.nvidia.com",
    "about.fb.com",
    "ai.google.dev",
    "huggingface.co",
    "github.com",
]

HEADING_PATTERN = re.compile(
    r"^##\s+\[(?P<title>.+?)\]\((?P<url>https?://[^)]+)\)(?:\s+`#(?P<rank>\d+)`)?\s*$",
    re.MULTILINE,
)
RELATED_LINK_PATTERN = re.compile(r"https?://[^\s)]+")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


class AiRssDailyPipeline:
    async def run(
        self,
        session,
        *,
        run_id: int | None = None,
        report_date: date | None = None,
        feed_url: str | None = None,
    ) -> Report:
        target_date = report_date or now_local().date()
        effective_feed_url = (feed_url or DEFAULT_AI_FEED_URL).strip() or DEFAULT_AI_FEED_URL

        retrieval_run = self._get_or_create_run(session, run_id=run_id, target_date=target_date)
        entries = await fetch_feed_entries(
            effective_feed_url,
            source_name="Juya AI Daily",
            source_type="ai",
        )
        issue = self._select_latest_issue(entries)
        issue_items = self._extract_issue_items(issue) if issue else []

        existing = session.scalars(
            select(Report).where(
                Report.report_date == target_date,
                Report.report_type == "ai",
            )
        ).all()
        for report in existing:
            session.delete(report)
        session.flush()

        issue_title = str(issue.get("title") or f"AI 日报 {target_date.isoformat()}") if issue else f"AI 日报 {target_date.isoformat()}"
        issue_body = self._normalize_issue_body(str(issue.get("snippet") or "")) if issue else ""
        report = Report(
            report_date=target_date,
            status="complete" if issue_items else "failed",
            title=issue_title,
            markdown_content=self._build_markdown(issue_title, issue_body, issue_items, effective_feed_url),
            summary=self._build_summary(issue_items, issue_title),
            pipeline_version="ai-rss-v2",
            retrieval_run_id=retrieval_run.id,
            report_type="ai",
            error_message=None if issue_items else "No parsable AI RSS issue found",
        )
        session.add(report)
        session.flush()

        for index, item_data in enumerate(issue_items, start=1):
            source_tier, reliability_label = self._classify_source_quality(item_data["source_url"])
            item = ReportItem(
                report_id=report.id,
                article_id=None,
                section=item_data["section"],
                rank=index,
                title=item_data["title"],
                source_name="Juya AI Daily",
                source_url=item_data["source_url"],
                published_at=issue.get("published_at") if issue else None,
                summary=item_data["summary"],
                research_signal=self._build_research_signal(item_data["section"]),
                image_url=None,
                image_source_url=None,
                image_origin_type=None,
                image_caption=None,
                image_relevance_score=0.0,
                has_verified_image=False,
                visual_verdict=None,
                context_verdict=None,
                selected_for_publish=True,
                image_reason=None,
                window_bucket="primary_24h",
                citations=[
                    {"label": "原文", "url": item_data["source_url"]},
                    *[
                        {"label": f"相关链接 {idx}", "url": url}
                        for idx, url in enumerate(item_data["related_links"], start=1)
                    ],
                ],
                combined_score=max(0.55, round(0.92 - index * 0.015, 3)),
                decision_trace={
                    "category": "AI",
                    "section": item_data["section"],
                    "source_domain": item_data["domain"],
                    "source_tier": source_tier,
                    "source_reliability_label": reliability_label,
                    "source_kind": "ai_rss_digest",
                    "page_kind": "article",
                    "evidence_strength": "rss_embedded_digest",
                    "supports_numeric_claims": False,
                    "allowed_for_trend_summary": True,
                    "selection_reason": "来自 AI 日报 RSS 内嵌分条，直接解析展示。",
                    "topic_confidence": "high",
                    "recency_status": "fresh",
                    "published_at_source": "rss",
                    "language": "zh",
                    "key_finding": item_data["summary"],
                    "search_query": effective_feed_url,
                    "evaluation_reason": "RSS 已提供分条摘要与原始链接，无需额外正文抓取。",
                },
                language="zh",
            )
            session.add(item)

        retrieval_run.status = report.status
        retrieval_run.finished_at = now_local().astimezone(UTC).replace(tzinfo=None)
        retrieval_run.query_count = 1
        retrieval_run.candidate_count = len(entries)
        retrieval_run.extracted_count = len(issue_items)
        retrieval_run.error_message = None if issue_items else "No parsable AI RSS issue found"
        retrieval_run.debug_payload = {
            "pipeline_version": "ai-rss-v2",
            "report_type": "ai",
            "feed_url": effective_feed_url,
            "fetched_count": len(entries),
            "selected_count": len(issue_items),
            "issue_title": issue_title,
            "sections": self._section_counts(issue_items),
        }
        session.commit()
        session.refresh(report)
        return report

    def _get_or_create_run(self, session, *, run_id: int | None, target_date: date) -> RetrievalRun:
        if run_id is not None:
            existing = session.get(RetrievalRun, run_id)
            if existing is not None:
                return existing

        run = RetrievalRun(
            run_date=datetime.combine(target_date, datetime.min.time()),
            status="running",
            shadow_mode=False,
        )
        session.add(run)
        session.flush()
        return run

    def _select_latest_issue(self, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        now = now_local().astimezone(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(hours=AI_RECENCY_HOURS)
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            url = canonicalize_url(str(entry.get("url") or ""))
            title = str(entry.get("title") or "").strip()
            snippet = str(entry.get("snippet") or "").strip()
            if not url or not title or not snippet:
                continue
            row = dict(entry)
            row["url"] = url
            normalized.append(row)

        fresh = [
            row for row in normalized
            if not isinstance(row.get("published_at"), datetime)
            or row["published_at"].astimezone(UTC).replace(tzinfo=None) >= cutoff
        ]
        chosen = fresh or normalized
        if not chosen:
            return None
        chosen.sort(
            key=lambda row: (
                row.get("published_at") is not None,
                row.get("published_at") or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return chosen[0]

    def _normalize_issue_body(self, value: str) -> str:
        text = html.unescape(value or "")
        if "<" in text and ">" in text:
            text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
            text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
            text = HTML_TAG_PATTERN.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract_issue_items(self, issue: dict[str, Any]) -> list[dict[str, Any]]:
        body = self._normalize_issue_body(str(issue.get("snippet") or ""))
        if not body:
            return []

        parse_body = body.split("* * *", 1)[1] if "* * *" in body else body
        matches = list(HEADING_PATTERN.finditer(parse_body))
        items: list[dict[str, Any]] = []
        for idx, match in enumerate(matches):
            block_start = match.end()
            block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(parse_body)
            block = parse_body[block_start:block_end].strip()
            title = match.group("title").strip()
            source_url = canonicalize_url(match.group("url").strip())
            if not title or not source_url:
                continue
            summary = self._extract_block_summary(block)
            section = self._classify_section(title=title, block=block)
            related_links = self._extract_related_links(block, primary_url=source_url)
            items.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "domain": self._extract_domain(source_url),
                    "summary": summary,
                    "section": section,
                    "related_links": related_links[:3],
                    "rank_hint": int(match.group("rank") or idx + 1),
                }
            )
        items.sort(key=lambda item: item["rank_hint"])
        return items[:MAX_AI_ITEMS]

    def _extract_block_summary(self, block: str) -> str:
        quote_lines = [
            line.lstrip("> ").strip()
            for line in block.splitlines()
            if line.strip().startswith(">")
        ]
        if quote_lines:
            return " ".join(quote_lines)[:320]

        paragraphs = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith(("相关链接", "*", "【"))
        ]
        if paragraphs:
            return paragraphs[0][:320]
        return "来自 AI RSS 的同步条目。"

    def _classify_section(self, *, title: str, block: str) -> str:
        text = f"{title} {block}".lower()
        if any(token in text for token in POLICY_TERMS):
            return "policy"
        if any(token in text for token in ACADEMIC_TERMS):
            return "academic"
        return "industry"

    def _extract_related_links(self, block: str, *, primary_url: str) -> list[str]:
        links = []
        for url in RELATED_LINK_PATTERN.findall(block):
            normalized = canonicalize_url(url.rstrip(".,"))
            if normalized and normalized != primary_url and normalized not in links:
                links.append(normalized)
        return links

    def _build_research_signal(self, section: str) -> str:
        if section == "academic":
            return "关注模型能力、研究结果与评测变化。"
        if section == "policy":
            return "关注 AI 治理、版权、安全与监管变化。"
        return "关注产品发布、生态动作与产业落地节奏。"

    def _build_summary(self, items: list[dict[str, Any]], issue_title: str) -> str:
        if not items:
            return "今日 AI RSS 暂无可解析条目。"
        counts = self._section_counts(items)
        return (
            f"{issue_title} 共解析 {len(items)} 条子资讯，"
            f"其中产业 {counts['industry']} 条、研究 {counts['academic']} 条、政策 {counts['policy']} 条。"
        )

    def _build_markdown(
        self,
        issue_title: str,
        issue_body: str,
        items: list[dict[str, Any]],
        feed_url: str,
    ) -> str:
        lines = [f"# {issue_title}", "", f"> RSS 来源：{feed_url}", ""]
        if issue_body:
            lines.append("## 快速概览")
            lines.append("")
            overview = issue_body.split("* * *", 1)[0].strip()
            if overview:
                lines.append(overview)
                lines.append("")
        if items:
            lines.append("## 拆分条目")
            lines.append("")
            for item in items:
                lines.append(f"- [{item['title']}]({item['source_url']})")
                lines.append(f"  - 分类：{item['section']}")
                lines.append(f"  - 摘要：{item['summary']}")
            lines.append("")
        return "\n".join(lines).strip()

    def _section_counts(self, items: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"industry": 0, "academic": 0, "policy": 0}
        for item in items:
            counts[item["section"]] += 1
        return counts

    def _classify_source_quality(self, source_url: str) -> tuple[str, str]:
        domain = self._extract_domain(source_url)
        if domain in SOCIAL_DOMAINS:
            return "B", "social_or_channel"
        if any(domain == pattern or domain.endswith(f".{pattern}") for pattern in OFFICIAL_DOMAIN_PATTERNS):
            return "A", "official_source"
        return "B", "secondary_source"

    def _extract_domain(self, url: str) -> str:
        return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/", 1)[0].lower())
