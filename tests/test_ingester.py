from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ingester import (
    ContinuousIngester,
    _BLOCKED_POOL_DOMAINS,
    _build_search_query_specs,
    _build_search_queries,
    _compute_content_hash,
    _is_weixin_url,
    _normalize_published_at,
    _row_is_relevant,
)


class TestComputeContentHash:
    def test_deterministic(self):
        h1 = _compute_content_hash("title", "https://example.com/a")
        h2 = _compute_content_hash("title", "https://example.com/a")
        assert h1 == h2

    def test_different_title_different_hash(self):
        h1 = _compute_content_hash("title A", "https://example.com/a")
        h2 = _compute_content_hash("title B", "https://example.com/a")
        assert h1 != h2

    def test_url_normalization(self):
        h1 = _compute_content_hash("t", "https://example.com/a?utm_source=x")
        h2 = _compute_content_hash("t", "https://example.com/a")
        assert h1 == h2


class TestIsWeixinUrl:
    def test_mp_weixin(self):
        assert _is_weixin_url("https://mp.weixin.qq.com/s/abc") is True

    def test_weixin_qq(self):
        assert _is_weixin_url("https://weixin.qq.com/something") is True

    def test_normal_url(self):
        assert _is_weixin_url("https://example.com/article") is False


class TestRowIsRelevant:
    def test_blocked_domain_rejected(self):
        domain = next(iter(_BLOCKED_POOL_DOMAINS))
        row = {"title": "polymer test", "snippet": "plastics", "url": f"https://{domain}/a"}
        assert _row_is_relevant(row) is False

    def test_no_positive_keyword_rejected(self):
        row = {"title": "random news", "snippet": "nothing here", "url": "https://good-domain.com/a"}
        with patch("app.services.ingester.classify_source", return_value={"page_kind": "article", "source_tier": "B"}):
            assert _row_is_relevant(row) is False

    def test_positive_keyword_accepted(self):
        row = {"title": "polymer recycling breakthrough", "snippet": "", "url": "https://good-domain.com/a"}
        with patch("app.services.ingester.classify_source", return_value={"page_kind": "article", "source_tier": "B"}):
            assert _row_is_relevant(row) is True

    def test_reject_page_kind(self):
        row = {"title": "polymer product page", "snippet": "", "url": "https://good-domain.com/product"}
        with patch("app.services.ingester.classify_source", return_value={"page_kind": "product", "source_tier": "B"}):
            assert _row_is_relevant(row) is False

    def test_tier_d_rejected(self):
        row = {"title": "polymer news", "snippet": "", "url": "https://good-domain.com/a"}
        with patch("app.services.ingester.classify_source", return_value={"page_kind": "article", "source_tier": "D"}):
            assert _row_is_relevant(row) is False

    def test_negative_only_rejected(self):
        row = {"title": "cagr stock forecast", "snippet": "market forecast", "url": "https://good-domain.com/a"}
        with patch("app.services.ingester.classify_source", return_value={"page_kind": "article", "source_tier": "B"}):
            assert _row_is_relevant(row) is False


class TestNormalizePublishedAt:
    def test_none_returns_none(self):
        assert _normalize_published_at(None) is None

    def test_datetime_naive_gets_utc(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = _normalize_published_at(dt)
        assert result.tzinfo == UTC

    def test_datetime_aware_keeps_tz(self):
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _normalize_published_at(dt)
        assert result == dt

    def test_string_iso_format(self):
        result = _normalize_published_at("2026-01-15T10:30:00")
        assert result is not None
        assert result.year == 2026

    def test_string_date_only(self):
        result = _normalize_published_at("2026-01-15")
        assert result is not None
        assert result.day == 15

    def test_invalid_string_returns_none(self):
        assert _normalize_published_at("not-a-date") is None


class TestBuildSearchQueries:
    def test_returns_zh_and_en(self):
        queries = _build_search_queries()
        assert "zh" in queries
        assert "en" in queries
        assert len(queries["zh"]) > 0
        assert len(queries["en"]) > 0

    def test_search_specs_include_intent_metadata_and_ai_lane(self):
        specs = _build_search_query_specs()
        assert all("section" in spec for spec in specs)
        assert all("category" in spec for spec in specs)
        assert all("query_family" in spec for spec in specs)
        assert any(spec["category"] == "AI" for spec in specs)


class TestBlockedDomains:
    def test_mysteel_blocked(self):
        row = {"title": "polymer test", "snippet": "plastics", "url": "https://mysteel.com/news/1"}
        assert _row_is_relevant(row) is False

    def test_foodmate_blocked(self):
        row = {"title": "polymer test", "snippet": "plastics", "url": "https://foodmate.net/news/1"}
        assert _row_is_relevant(row) is False

    def test_social_media_blocked(self):
        for domain in ("linkedin.com", "facebook.com", "twitter.com", "weibo.com", "douyin.com"):
            row = {"title": "polymer test", "snippet": "plastics", "url": f"https://{domain}/post/1"}
            assert _row_is_relevant(row) is False, f"{domain} should be blocked"

    def test_job_sites_blocked(self):
        for domain in ("zhipin.com", "liepin.com", "51job.com", "zhaopin.com"):
            row = {"title": "polymer test", "snippet": "plastics", "url": f"https://{domain}/job/1"}
            assert _row_is_relevant(row) is False, f"{domain} should be blocked"

    def test_business_info_blocked(self):
        for domain in ("qcc.com", "tianyancha.com", "aiqicha.baidu.com"):
            row = {"title": "polymer test", "snippet": "plastics", "url": f"https://{domain}/firm/1"}
            assert _row_is_relevant(row) is False, f"{domain} should be blocked"


class TestContinuousIngester:
    @pytest.mark.asyncio
    async def test_ingest_rss_feeds(self):
        ingester = ContinuousIngester()
        mock_sources = [
            MagicMock(
                enabled=True,
                rss_or_listing_url="https://example.com/feed.xml",
                use_direct_source=True,
                crawl_mode="rss",
                source_tier="top-industry-media",
                priority=5,
                name="TestSource",
                type="news",
                language="zh",
            )
        ]
        mock_entries = [
            {"url": "https://example.com/a1", "title": "polymer recycling news", "snippet": "plastics"},
        ]
        with (
            patch("app.services.ingester.list_sources", return_value=mock_sources),
            patch("app.services.ingester.fetch_feed_entries", new_callable=AsyncMock, return_value=mock_entries),
            patch("app.services.ingester._row_is_relevant", return_value=True),
            patch.object(ingester, "_try_write_pool", new_callable=AsyncMock, return_value=1),
        ):
            result = await ingester._ingest_rss()
            assert result == 1

    @pytest.mark.asyncio
    async def test_ingest_rss_empty_sources(self):
        ingester = ContinuousIngester()
        with patch("app.services.ingester.list_sources", return_value=[]):
            result = await ingester._ingest_rss()
            assert result == 0

    @pytest.mark.asyncio
    async def test_url_dedup_in_try_write_pool(self):
        ingester = ContinuousIngester()
        with (
            patch("app.services.ingester.canonicalize_url", return_value="https://example.com/a"),
            patch("app.services.ingester.session_scope") as mock_scope,
        ):
            mock_session = MagicMock()
            mock_session.scalars.return_value.first.return_value = MagicMock()
            mock_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)
            result = await ingester._try_write_pool(
                url="https://example.com/a",
                title="Test",
                domain="example.com",
                source_type="rss",
                language="zh",
            )
            assert result == 0

    @pytest.mark.asyncio
    async def test_domain_blacklist_in_try_write_pool(self):
        ingester = ContinuousIngester()
        blocked = next(iter(_BLOCKED_POOL_DOMAINS))
        result = await ingester._try_write_pool(
            url=f"https://{blocked}/a",
                title="Test",
                domain=blocked,
                source_type="rss",
                language="zh",
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_template_search(self):
        ingester = ContinuousIngester()
        mock_engine = MagicMock()
        mock_engine.batch_search = AsyncMock(return_value=[
            {"url": "https://example.com/s1", "title": "polymer news", "snippet": "plastics", "domain": "example.com"},
        ])
        ingester._search_engine = mock_engine
        with (
            patch("app.services.ingester._row_is_relevant", return_value=True),
            patch.object(ingester, "_try_write_pool", new_callable=AsyncMock, return_value=1),
        ):
            result = await ingester._ingest_template_searches()
            assert result >= 1

    @pytest.mark.asyncio
    async def test_try_write_pool_fills_source_tier(self):
        ingester = ContinuousIngester()
        mock_quality = {
            "source_tier": "A",
            "source_kind": "academic_journal",
            "page_kind": "article",
        }
        with (
            patch("app.services.ingester.canonicalize_url", return_value="https://example.com/a"),
            patch("app.services.ingester.classify_source", return_value=mock_quality),
            patch("app.services.ingester.session_scope") as mock_scope,
            patch.object(ingester, "_fetch_article_content", new_callable=AsyncMock),
        ):
            mock_session = MagicMock()
            mock_session.scalars.return_value.first.return_value = None
            mock_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)
            result = await ingester._try_write_pool(
                url="https://example.com/a",
                title="Polymer study",
                domain="example.com",
                source_type="rss",
                language="en",
                snippet="A new study on polymers.",
            )
            assert result == 1
            added_article = mock_session.add.call_args[0][0]
            assert added_article.source_tier == "A"
            assert added_article.source_kind == "academic_journal"
            assert added_article.page_kind == "article"
