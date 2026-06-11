from __future__ import annotations

import pytest

from app.services.source_quality import (
    SOURCE_TIER_RANK,
    classify_source,
    detect_page_kind,
    detect_source_kind,
    infer_source_tier,
)


class TestClassifySourceHighTier:
    def test_gov_cn_domain(self):
        result = classify_source(url="https://www.miit.gov.cn/news", title="政策通知", content="")
        assert result["source_kind"] == "government"
        assert result["source_tier"] == "A"

    def test_academic_journal(self):
        result = classify_source(url="https://nature.com/articles/s41586-026", title="Polymer study", content="")
        assert result["source_kind"] == "academic_journal"
        assert result["source_tier"] == "A"

    def test_top_industry_media(self):
        result = classify_source(url="https://plasticsnews.com/article/123", title="Industry news", content="")
        assert result["source_kind"] == "top_industry_media"
        assert result["source_tier"] == "A"


class TestClassifySourceLowTier:
    def test_ecommerce_domain(self):
        result = classify_source(url="https://taobao.com/product/123", title="Product", content="")
        assert result["source_tier"] == "D"

    def test_download_page(self):
        result = classify_source(url="https://example.com/download/file.pdf", title="Download", content="")
        assert result["source_tier"] == "D"

    def test_homepage(self):
        result = classify_source(url="https://example.com/", title="Home", content="")
        assert result["source_tier"] == "D"


class TestDomainTierRank:
    def test_tier_rank_ordering(self):
        assert SOURCE_TIER_RANK["A"] > SOURCE_TIER_RANK["B"]
        assert SOURCE_TIER_RANK["B"] > SOURCE_TIER_RANK["C"]
        assert SOURCE_TIER_RANK["C"] > SOURCE_TIER_RANK["D"]

    def test_all_tiers_present(self):
        assert set(SOURCE_TIER_RANK.keys()) == {"A", "B", "C", "D"}


class TestDetectPageKind:
    def test_news_path(self):
        assert detect_page_kind("https://example.com/news/polymer-article") == "news"

    def test_product_path(self):
        assert detect_page_kind("https://example.com/products/injection-mold") == "product"

    def test_homepage(self):
        assert detect_page_kind("https://example.com/") == "homepage"

    def test_pdf_download(self):
        assert detect_page_kind("https://example.com/files/report.pdf") == "download"

    def test_article_default(self):
        assert detect_page_kind("https://example.com/2026/01/polymer-update") == "article"


class TestDetectSourceKind:
    def test_gov_domain(self):
        assert detect_source_kind("epa.gov", "news") == "government"

    def test_edu_domain(self):
        assert detect_source_kind("mit.edu", "article") == "academic"

    def test_general_site(self):
        assert detect_source_kind("random-blog.com", "article") == "general_site"


class TestInferSourceTier:
    def test_government_is_a(self):
        assert infer_source_tier("gov.cn", "news", "government") == "A"

    def test_general_site_is_c(self):
        assert infer_source_tier("random.com", "article", "general_site") == "C"

    def test_ecommerce_is_d(self):
        assert infer_source_tier("shop.com", "product", "ecommerce") == "D"
