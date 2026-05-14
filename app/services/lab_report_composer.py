"""Lightweight lab daily report composer.

Deterministic pipeline: patent rotation + article selection + optional LLM polish.
No Agent loop — runs in seconds, not minutes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from app.database import session_scope
from app.models import AppSetting, ArticlePool, Patent, Report, ReportItem, WeChatArticle
from app.utils import now_local

logger = logging.getLogger(__name__)

# 11 categories with enough patents for rotation
_LAB_CATEGORIES = [
    "静电纺丝", "注塑", "复合材料", "传热", "轮胎",
    "3D打印", "纳米", "模具硫化", "挤出", "口罩过滤",
    "航空航天",
]

# Section ordering for lab reports
_LAB_SECTIONS = ["patent", "wechat", "lab_news"]

# Lab-related keywords for filtering ArticlePool
_LAB_KEYWORDS = ["英蓝", "北京化工", "BUCT", "杨卫民", "北化工", "buct", "yinglan"]

_PATENT_URL_TEMPLATE = "https://patents.google.com/patent/{publication_number}"
_PATENT_URL_CNIPA = "https://cpquery.cpa.org.cn/txnQueryBibliographicData.do?select-key:shenqingh={patent_number}"


def daily_category(target_date: date) -> str:
    return _LAB_CATEGORIES[target_date.timetuple().tm_yday % len(_LAB_CATEGORIES)]


def _get_cursor(session, key: str) -> int:
    setting = session.get(AppSetting, f"lab_cursor_{key}")
    if setting and isinstance(setting.value, dict):
        return int(setting.value.get("position", 0))
    return 0


def _set_cursor(session, key: str, position: int):
    key_name = f"lab_cursor_{key}"
    setting = session.get(AppSetting, key_name)
    if setting:
        setting.value = {"position": position}
    else:
        session.add(AppSetting(key=key_name, value={"position": position}))


def _patent_url(p: Patent) -> str:
    if p.publication_number:
        return _PATENT_URL_TEMPLATE.format(publication_number=p.publication_number)
    return _PATENT_URL_CNIPA.format(patent_number=p.patent_number)


class LabReportComposer:
    """Composes a daily lab report from patents and lab-related articles."""

    def compose(self, target_date: date | None = None) -> Report | None:
        target = target_date or now_local().date()
        logger.info("LabReportComposer: composing for %s", target)

        category = daily_category(target)
        logger.info("Today's theme: %s", category)

        with session_scope() as session:
            # Check if lab report already exists for today
            existing = session.scalar(
                select(Report)
                .where(Report.report_date == target, Report.report_type == "lab")
                .limit(1)
            )
            if existing:
                logger.info("Lab report already exists for %s (id=%d), skipping.", target, existing.id)
                return existing

            # 1. Select patents
            patents = self._select_patents(session, category, count=4)
            logger.info("Selected %d patents for category '%s'", len(patents), category)

            # 2. Select WeChat articles
            wechat_items = self._select_wechat_articles(session, limit=2)
            logger.info("Selected %d WeChat articles", len(wechat_items))

            # 3. Select lab-related articles
            lab_items = self._select_lab_articles(session, limit=2)
            logger.info("Selected %d lab-related articles", len(lab_items))

            if not patents and not wechat_items and not lab_items:
                logger.warning("No content available for lab report, skipping.")
                return None

            # 4. Assemble report
            report = self._build_report(session, target, category, patents, wechat_items, lab_items)
            logger.info("Lab report created: id=%d, title='%s'", report.id, report.title)
            return report

    def _select_patents(self, session, category: str, count: int = 4) -> list[Patent]:
        patents = list(session.scalars(
            select(Patent)
            .where(Patent.category == category)
            .order_by(desc(Patent.grant_date))
        ).all())
        if not patents:
            return []

        cursor_key = f"patent_{category}"
        cursor = _get_cursor(session, cursor_key)

        selected = []
        for i in range(min(count, len(patents))):
            idx = (cursor + i) % len(patents)
            selected.append(patents[idx])

        _set_cursor(session, cursor_key, (cursor + count) % len(patents))
        return selected

    def _select_wechat_articles(self, session, limit: int = 2) -> list[dict[str, Any]]:
        # Try ArticlePool first (promoted articles with full content)
        articles = list(session.scalars(
            select(ArticlePool)
            .where(ArticlePool.source_type == "wechat")
            .order_by(desc(ArticlePool.published_at))
            .limit(limit)
        ).all())
        if articles:
            return [self._article_to_item(a, section="wechat") for a in articles]

        # Fallback: read directly from WeChatArticle staging table
        wechat_articles = list(session.scalars(
            select(WeChatArticle)
            .where(WeChatArticle.scrape_status.in_(["scraped", "promoted"]))
            .order_by(desc(WeChatArticle.published_at))
            .limit(limit)
        ).all())
        return [
            {
                "section": "wechat",
                "title": wa.title,
                "source_name": wa.account_name,
                "source_url": wa.url,
                "published_at": wa.published_at,
                "summary": wa.summary or "",
                "research_signal": "",
                "image_url": wa.image_url,
                "language": "zh",
                "decision_trace": {
                    "category": "高材制造",
                    "source_type": "wechat",
                    "source_domain": "mp.weixin.qq.com",
                },
            }
            for wa in wechat_articles
        ]

    def _select_lab_articles(self, session, limit: int = 2) -> list[dict[str, Any]]:
        conditions = [
            ArticlePool.title.ilike(f"%{kw}%") | ArticlePool.summary.ilike(f"%{kw}%")
            for kw in _LAB_KEYWORDS
        ]
        from sqlalchemy import or_
        articles = list(session.scalars(
            select(ArticlePool)
            .where(or_(*conditions))
            .where(ArticlePool.source_type != "wechat")
            .order_by(desc(ArticlePool.published_at))
            .limit(limit)
        ).all())
        return [self._article_to_item(a, section="lab_news") for a in articles]

    def _article_to_item(self, a: ArticlePool, section: str) -> dict[str, Any]:
        return {
            "section": section,
            "title": a.title,
            "source_name": a.domain,
            "source_url": a.url,
            "published_at": a.published_at,
            "summary": a.summary or "",
            "research_signal": "",
            "image_url": None,
            "language": a.language or "zh",
            "decision_trace": {
                "category": "高材制造",
                "source_type": a.source_type,
                "source_domain": a.domain,
            },
        }

    def _build_report(
        self,
        session,
        target_date: date,
        category: str,
        patents: list[Patent],
        wechat_items: list[dict[str, Any]],
        lab_items: list[dict[str, Any]],
    ) -> Report:
        # Build markdown content
        md_parts = [f"# 实验室日报 — {category}专题 ({target_date})\n"]

        if patents:
            md_parts.append(f"\n## 专利精选 — {category}\n")
            for i, p in enumerate(patents, 1):
                inventors_short = p.inventors[:50] + ("..." if len(p.inventors) > 50 else "")
                md_parts.append(f"### {i}. {p.name}\n")
                md_parts.append(f"- **专利号**: {p.patent_number}")
                if p.publication_number:
                    md_parts.append(f"- **公开号**: {p.publication_number}")
                if p.grant_date:
                    md_parts.append(f"- **授权日**: {p.grant_date}")
                md_parts.append(f"- **发明人**: {inventors_short}")
                md_parts.append(f"- **技术类别**: {p.category}\n")

        if wechat_items:
            md_parts.append("\n## 英蓝云展动态\n")
            for item in wechat_items:
                md_parts.append(f"### {item['title']}\n")
                if item.get("summary"):
                    md_parts.append(f"{item['summary']}\n")

        if lab_items:
            md_parts.append("\n## 实验室相关资讯\n")
            for item in lab_items:
                md_parts.append(f"### {item['title']}\n")
                if item.get("summary"):
                    md_parts.append(f"{item['summary']}\n")

        markdown_content = "\n".join(md_parts)

        # Build summary
        summary_parts = [f"今日主题：{category}"]
        if patents:
            summary_parts.append(f"精选 {len(patents)} 项专利")
        if wechat_items:
            summary_parts.append(f"{len(wechat_items)} 篇公众号文章")
        if lab_items:
            summary_parts.append(f"{len(lab_items)} 条实验室资讯")
        summary = "，".join(summary_parts)

        # Create Report
        report = Report(
            report_date=target_date,
            status="complete_auto_publish",
            title=f"实验室日报 — {category}专题 ({target_date})",
            markdown_content=markdown_content,
            summary=summary,
            pipeline_version="lab-v1",
            report_type="lab",
        )
        session.add(report)
        session.flush()

        # Create ReportItems
        rank = 1

        # Patent items
        for p in patents:
            item = ReportItem(
                report_id=report.id,
                section="patent",
                rank=rank,
                title=p.name,
                source_name="专利",
                source_url=_patent_url(p),
                published_at=datetime.combine(p.grant_date, datetime.min.time()) if p.grant_date else None,
                summary=f"发明人：{p.inventors[:80]}",
                research_signal=f"技术类别：{p.category}，专利号：{p.patent_number}",
                language="zh",
                decision_trace={
                    "category": category,
                    "patent_number": p.patent_number,
                    "publication_number": p.publication_number,
                    "inventors": p.inventors,
                    "patent_category": p.category,
                    "grant_date": str(p.grant_date) if p.grant_date else None,
                },
            )
            session.add(item)
            rank += 1

        # WeChat items
        for data in wechat_items:
            item = ReportItem(
                report_id=report.id,
                rank=rank,
                combined_score=0.6,
                **data,
            )
            session.add(item)
            rank += 1

        # Lab news items
        for data in lab_items:
            item = ReportItem(
                report_id=report.id,
                rank=rank,
                combined_score=0.5,
                **data,
            )
            session.add(item)
            rank += 1

        return report
