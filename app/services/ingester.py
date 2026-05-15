from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.database import session_scope
from app.models import ArticlePool, WeChatArticle
from app.services.repository import list_sources
from app.services.rss import fetch_feed_entries
from app.services.source_quality import classify_source
from app.utils import canonicalize_url, extract_domain, now_local

logger = logging.getLogger(__name__)

_TRUSTED_SOURCE_TIER_RANK = {
    "government": 5,
    "standards": 4,
    "academic-journal": 4,
    "top-industry-media": 3,
    "company-newsroom": 2,
    "unknown": 1,
    "pr-wire": 0,
}
_TRUSTED_SOURCE_SEED_LIMIT = 20
_TRUSTED_SOURCE_ITEMS_PER_FEED = 5
_RSS_FAILURE_THRESHOLD = 3

_rss_consecutive_failures: dict[str, int] = {}

_POSITIVE_KEYWORDS = [
    "高分子", "塑料", "树脂", "改性", "注塑", "挤出", "吹塑",
    "复合材料", "recycling", "polymer", "plastics", "resin",
    "extrusion", "injection", "processing", "materials",
    "材料", "化工", "化学", "橡胶", "纤维", "薄膜",
    "macromolecule", "macromolecular", "elastomer", "thermoplastic",
    "monomer", "copolymer", "polyolefin", "polyurethane",
    "polystyrene", "polyester", "polyamide", "polycarbonate",
    "nanocomposite", "bioplastic", "biopolymer", "degradable",
    "sustainability", "circular economy",
]
_NEGATIVE_KEYWORDS = [
    "market forecast", "cagr", "stock", "earnings", "marathon",
    "football", "soccer", "war", "missile", "ophthalmology",
    "biogen", "apellis", "pharma", "财经", "股价", "财报",
    "马拉松", "足球", "战争", "导弹", "医药",
]
_PREVIEW_REJECT_PAGE_KINDS = {
    "download", "search", "product", "about", "homepage",
    "navigation", "anti_bot", "binary",
}

_BLOCKED_POOL_DOMAINS: set[str] = {
    "peipusci.com", "renzhengyun.cn", "xiaokudang.com",
    "texleader.com.cn", "yoojia.com", "ssoocc.com",
    "quheqihuo.com", "fucaiyunji.com", "xjishu.com",
    "lz.gxrc.com", "yszc.com.cn", "zixin.com.cn",
    "bk.taobao.com", "industrystock.cn", "cmpe360.com",
    "m.chinairn.com", "cn.agropages.com", "m.zhixuedoc.com",
    "eduour.com", "scholar.xjtu.edu.cn",
}


def _compute_content_hash(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{canonicalize_url(url)}".encode()).hexdigest()


def _row_is_relevant(row: dict[str, Any]) -> bool:
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or "")
    url = str(row.get("url") or "")
    domain = extract_domain(url)
    if domain in _BLOCKED_POOL_DOMAINS:
        return False
    text = f"{title} {snippet}".lower()
    if not any(keyword.lower() in text for keyword in _POSITIVE_KEYWORDS):
        return False
    quality = classify_source(url=url, title=title, content=snippet)
    if quality["page_kind"] in _PREVIEW_REJECT_PAGE_KINDS:
        return False
    if quality["source_tier"] == "D":
        return False
    negative_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw.lower() in text)
    positive_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw.lower() in text)
    return not (negative_hits > 0 and positive_hits == 0)


class ContinuousIngester:
    def __init__(self) -> None:
        self._search_engine = None

    @property
    def search_engine(self):
        if self._search_engine is None:
            from app.services.bocha_search import BochaSearchClient
            from app.services.search_engine import SearchEngine
            self._search_engine = SearchEngine(
                bocha_client=BochaSearchClient(),
            )
        return self._search_engine

    async def run(self) -> int:
        total_ingested = 0
        total_ingested += await self._ingest_rss()
        total_ingested += await self._ingest_template_searches()
        logger.info("ContinuousIngester: ingested %d new articles", total_ingested)
        return total_ingested

    async def _ingest_rss(self) -> int:
        try:
            with session_scope() as session:
                sources = list_sources(session)
        except Exception as exc:
            logger.warning("ContinuousIngester: failed to load sources: %s", exc)
            return 0

        trusted = [
            s for s in sources
            if s.enabled and s.rss_or_listing_url
            and (s.use_direct_source or s.crawl_mode == "rss")
        ]
        trusted.sort(
            key=lambda s: (
                _TRUSTED_SOURCE_TIER_RANK.get(str(s.source_tier or "unknown"), 1),
                int(s.priority or 0),
            ),
            reverse=True,
        )
        selected = trusted[:_TRUSTED_SOURCE_SEED_LIMIT]
        if not selected:
            return 0

        ingested = 0
        active = [
            s for s in selected
            if _rss_consecutive_failures.get(str(s.rss_or_listing_url), 0) < _RSS_FAILURE_THRESHOLD
        ]
        skipped = len(selected) - len(active)
        if skipped:
            logger.info("RSS: skipping %d feeds with %d+ consecutive failures", skipped, _RSS_FAILURE_THRESHOLD)
        results = await asyncio.gather(
            *[fetch_feed_entries(str(s.rss_or_listing_url), s.name, s.type) for s in active],
            return_exceptions=True,
        )
        for source, result in zip(active, results, strict=False):
            feed_url = str(source.rss_or_listing_url)
            if isinstance(result, Exception):
                _rss_consecutive_failures[feed_url] = _rss_consecutive_failures.get(feed_url, 0) + 1
                if _rss_consecutive_failures[feed_url] >= _RSS_FAILURE_THRESHOLD:
                    logger.warning(
                        "RSS feed disabled after %d consecutive failures: %s (%s): %s",
                        _rss_consecutive_failures[feed_url], source.name, feed_url, result,
                    )
                else:
                    logger.warning("RSS feed failed for %s (%s): %s", source.name, feed_url, result)
                continue
            elif isinstance(result, list):
                _rss_consecutive_failures.pop(feed_url, None)
                logger.info("RSS feed OK for %s: %d entries", source.name, len(result))
            else:
                continue
            for row in result[:_TRUSTED_SOURCE_ITEMS_PER_FEED]:
                if not _row_is_relevant(row):
                    continue
                ingested += await self._try_write_pool(
                    url=str(row.get("url") or ""),
                    title=str(row.get("title") or ""),
                    domain=extract_domain(str(row.get("url") or "")),
                    source_type="rss",
                    language="zh" if source.language is None else str(source.language or "zh"),
                    snippet=str(row.get("snippet") or ""),
                    published_at=row.get("published_at"),
                )
        return ingested

    async def _ingest_template_searches(self) -> int:
        try:
            from app.services.search_engine import SearchEngine
        except ImportError:
            logger.warning("ContinuousIngester: SearchEngine not available, skipping template searches")
            return 0

        ingested = 0
        search_engine = self.search_engine
        queries = _build_search_queries()
        for lang, lang_queries in queries.items():
            results = await search_engine.batch_search(
                lang_queries, language=lang, max_results=8, concurrency=3,
            )
            for r in results:
                if not _row_is_relevant(r):
                    continue
                url = str(r.get("url") or "")
                if _is_weixin_url(url):
                    ingested += await self._try_write_wechat(
                        url=url,
                        title=str(r.get("title") or ""),
                        snippet=str(r.get("snippet") or ""),
                        published_at=r.get("published_at"),
                    )
                else:
                    ingested += await self._try_write_pool(
                        url=url,
                        title=str(r.get("title") or ""),
                        domain=str(r.get("domain") or extract_domain(url)),
                        source_type="template_search",
                        language=lang,
                        snippet=str(r.get("snippet") or ""),
                        published_at=r.get("published_at"),
                    )
        return ingested

    async def _try_write_pool(
        self, *, url: str, title: str, domain: str, source_type: str,
        language: str, snippet: str = "", published_at: Any = None,
    ) -> int:
        if domain in _BLOCKED_POOL_DOMAINS:
            return 0
        normalized = canonicalize_url(url)
        if not normalized:
            return 0
        content_hash = _compute_content_hash(title, normalized)
        try:
            with session_scope() as session:
                existing = session.scalars(
                    select(ArticlePool).where(ArticlePool.url == normalized)
                ).first()
                if existing is not None:
                    return 0
                article = ArticlePool(
                    url=normalized,
                    content_hash=content_hash,
                    title=title,
                    domain=domain,
                    source_type=source_type,
                    language=language,
                    summary=snippet,
                    published_at=_normalize_published_at(published_at),
                    ingested_at=now_local(),
                )
                session.add(article)
                session.commit()
                return 1
        except Exception as exc:
            logger.debug("ContinuousIngester: skip duplicate %s: %s", normalized, exc)
            return 0

    async def _try_write_wechat(
        self, *, url: str, title: str, snippet: str = "", published_at: Any = None,
    ) -> int:
        normalized = canonicalize_url(url)
        if not normalized:
            return 0
        try:
            with session_scope() as session:
                existing = session.scalar(
                    select(WeChatArticle).where(WeChatArticle.url == normalized)
                )
                if existing:
                    return 0
                session.add(WeChatArticle(
                    url=normalized,
                    title=title,
                    account_name="英蓝云展",
                    published_at=_normalize_published_at(published_at),
                    scrape_status="pending",
                    summary=snippet[:200] if snippet else None,
                ))
                session.commit()
                return 1
        except Exception as exc:
            logger.debug("ContinuousIngester: skip duplicate wechat %s: %s", normalized, exc)
            return 0


def _is_weixin_url(url: str) -> bool:
    return "mp.weixin.qq.com" in url or "weixin.qq.com" in url


async def ingest_weixin_articles() -> int:
    """Scrape pending WeChatArticle entries and promote to ArticlePool."""
    from app.services.scraper import ScraperClient
    scraper = ScraperClient()
    processed = 0

    with session_scope() as session:
        pending = list(session.scalars(
            select(WeChatArticle)
            .where(WeChatArticle.scrape_status == "pending")
            .limit(10)
        ).all())

    for wa in pending:
        try:
            result = await scraper.scrape(wa.url)
            if result and result.get("markdown"):
                with session_scope() as session:
                    db_wa = session.get(WeChatArticle, wa.id)
                    if not db_wa:
                        continue
                    db_wa.raw_content = result["markdown"]
                    db_wa.summary = result.get("title", wa.title)[:200]
                    db_wa.scraped_at = now_local()
                    db_wa.scrape_status = "scraped"

                    # Promote to ArticlePool
                    normalized = canonicalize_url(wa.url)
                    content_hash = hashlib.sha256((wa.title + normalized).encode()).hexdigest()
                    existing = session.scalar(
                        select(ArticlePool).where(ArticlePool.url == normalized)
                    )
                    if not existing:
                        article = ArticlePool(
                            url=normalized,
                            content_hash=content_hash,
                            title=wa.title,
                            domain="mp.weixin.qq.com",
                            source_type="wechat",
                            language="zh",
                            raw_content=result["markdown"][:50000],
                            summary=wa.summary or result.get("title", "")[:200],
                            published_at=wa.published_at,
                            ingested_at=now_local(),
                        )
                        session.add(article)
                        session.flush()
                        db_wa.article_pool_id = article.id
                        db_wa.scrape_status = "promoted"
                    else:
                        db_wa.scrape_status = "promoted"
                        db_wa.article_pool_id = existing.id
                    session.commit()
                processed += 1
                logger.info("WeChat: scraped and promoted '%s'", wa.title[:50])
            else:
                with session_scope() as session:
                    db_wa = session.get(WeChatArticle, wa.id)
                    if db_wa:
                        db_wa.scrape_status = "failed"
                        session.commit()
                logger.warning("WeChat: scrape failed for '%s'", wa.title[:50])
        except Exception as exc:
            logger.warning("WeChat: error scraping '%s': %s", wa.title[:50], exc)
            with session_scope() as session:
                db_wa = session.get(WeChatArticle, wa.id)
                if db_wa:
                    db_wa.scrape_status = "failed"
                    session.commit()

    return processed


def _normalize_published_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value[:19], fmt)
                return dt.replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _build_search_queries() -> dict[str, list[str]]:
    return {
        "zh": [
            "注塑机 新品发布", "挤出设备 技术升级", "高分子材料 产能扩建",
            "塑料原料 价格行情", "复合材料 汽车轻量化", "改性塑料 应用",
            "限塑令 最新政策", "碳关税 塑料行业", "环保法规 高分子材料",
            "高分子改性 研究进展", "聚合物 新材料 论文",
            "北京化工大学 英蓝实验室 高分子 研究",
            "英蓝云展 高分子材料 加工技术",
        ],
        "en": [
            "injection molding machine new product",
            "polymer processing equipment innovation",
            "plastics recycling technology breakthrough",
            "EU plastic regulation policy",
            "carbon border tax polymer industry",
            "polymer composite materials science research",
            "Beijing University Chemical Technology polymer processing",
            "Yinglan laboratory polymer research",
        ],
    }
