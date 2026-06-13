from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.explorer_agent import (
    ExplorerAgent,
    SECTION_CATEGORY_QUERIES,
    _build_explorer_prompt,
    get_search_queries,
)


class TestSearchQueryGeneration:
    def test_industry_high_material_returns_queries(self):
        queries = get_search_queries("industry", "高材制造")
        assert len(queries) == 5
        assert all("query" in q and "language" in q for q in queries)
        assert queries[0]["query"] == "注塑机 新品 2026"

    def test_policy_clean_energy_returns_queries(self):
        queries = get_search_queries("policy", "清洁能源")
        assert len(queries) == 4
        assert any("碳关税" in q["query"] for q in queries)

    def test_academic_ai_returns_queries(self):
        queries = get_search_queries("academic", "AI")
        assert len(queries) == 3
        assert all(q["language"] == "en" for q in queries)

    def test_unknown_section_returns_empty(self):
        queries = get_search_queries("unknown", "unknown")
        assert queries == []

    def test_all_query_keys_are_section_category_tuples(self):
        for key in SECTION_CATEGORY_QUERIES:
            assert isinstance(key, tuple)
            assert len(key) == 2
            section, category = key
            assert section in {"industry", "policy", "academic"}


class TestSourceQualityFiltering:
    def test_filters_d_tier(self):
        article = {
            "title": "Test article",
            "url": "https://taobao.com/news/1",
            "domain": "taobao.com",
            "source_name": "Taobao",
            "summary": "polymer processing content",
            "key_finding": "new development",
            "source_tier": "D",
            "source_kind": "ecommerce",
            "selection_reason": "relevant",
            "image_url": None,
            "published_at": None,
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is None

    def test_filters_homepage_page_kind(self):
        article = {
            "title": "Example.com",
            "url": "https://example.com/",
            "domain": "example.com",
            "source_name": "Example",
            "summary": "",
            "key_finding": "",
            "source_tier": "B",
            "source_kind": "general_site",
            "selection_reason": "",
            "image_url": None,
            "published_at": None,
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is None

    def test_filters_product_page_kind(self):
        article = {
            "title": "Product page",
            "url": "https://example.com/product/123",
            "domain": "example.com",
            "source_name": "Example",
            "summary": "product listing",
            "key_finding": "",
            "source_tier": "B",
            "source_kind": "general_site",
            "selection_reason": "",
            "image_url": None,
            "published_at": None,
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is None

    def test_accepts_valid_article(self):
        article = {
            "title": "新型注塑机发布",
            "url": "https://ptonline.com/news/2026/injection-molding",
            "domain": "ptonline.com",
            "source_name": "Plastics Technology",
            "summary": "A new injection molding machine with AI capabilities",
            "key_finding": "AI-driven process optimization reduces waste by 15%",
            "source_tier": "A",
            "source_kind": "industry_media",
            "selection_reason": "行业领先媒体首次报道AI注塑技术",
            "image_url": "https://ptonline.com/img/injection.jpg",
            "published_at": "2026-06-13T10:00:00Z",
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is not None
        assert result["title"] == "新型注塑机发布"
        assert result["source_tier"] == "A"
        assert result["why_selected"] == "行业领先媒体首次报道AI注塑技术"

    def test_missing_url_returns_none(self):
        article = {
            "title": "No URL article",
            "url": "",
            "summary": "content",
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is None

    def test_missing_title_returns_none(self):
        article = {
            "title": "",
            "url": "https://example.com/news/1",
            "summary": "content",
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is None


class TestCandidateOutput:
    def test_candidate_has_all_required_fields(self):
        article = {
            "title": "Polymer breakthrough",
            "url": "https://nature.com/articles/polymer-2026",
            "domain": "nature.com",
            "source_name": "Nature",
            "summary": "A breakthrough in polymer processing",
            "key_finding": "New catalyst improves reaction speed",
            "source_tier": "A",
            "source_kind": "academic_journal",
            "selection_reason": "顶刊首次报道新型催化剂",
            "image_url": "https://nature.com/img/polymer.jpg",
            "published_at": "2026-06-12T08:00:00Z",
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is not None
        required_fields = [
            "title", "url", "domain", "source_name", "summary",
            "key_finding", "source_tier", "source_kind", "why_selected",
            "image_url", "published_at",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_why_selected_fallback_uses_key_finding(self):
        article = {
            "title": "Some article",
            "url": "https://ptonline.com/news/2",
            "domain": "ptonline.com",
            "source_name": "Plastics Technology",
            "summary": "polymer content here for testing purposes",
            "key_finding": "Important discovery",
            "source_tier": "A",
            "source_kind": "top_industry_media",
            "selection_reason": "",
            "image_url": None,
            "published_at": None,
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is not None
        assert "Important discovery" in result["why_selected"]
        assert "A" in result["why_selected"]

    def test_why_selected_fallback_without_key_finding(self):
        article = {
            "title": "Another article",
            "url": "https://example.com/news/3",
            "domain": "example.com",
            "source_name": "Example",
            "summary": "some polymer content here for testing",
            "key_finding": "",
            "source_tier": "C",
            "source_kind": "general_site",
            "selection_reason": "",
            "image_url": None,
            "published_at": None,
        }
        result = ExplorerAgent._article_to_candidate(article)
        assert result is not None
        assert "C" in result["why_selected"]


class TestExplorerAgentInit:
    def test_init_with_defaults(self):
        agent = ExplorerAgent(section="industry", category="高材制造")
        assert agent.section == "industry"
        assert agent.category == "高材制造"
        assert agent._llm is not None

    def test_init_with_custom_llm(self):
        mock_llm = SimpleNamespace()
        agent = ExplorerAgent(
            section="policy",
            category="清洁能源",
            llm_client=mock_llm,
        )
        assert agent._llm is mock_llm

    def test_system_prompt_contains_section_and_category(self):
        prompt = _build_explorer_prompt("industry", "高材制造")
        assert "industry" in prompt
        assert "高材制造" in prompt

    def test_task_prompt_contains_queries(self):
        agent = ExplorerAgent(section="industry", category="高材制造")
        prompt = agent._build_task_prompt()
        assert "注塑机 新品 2026" in prompt
        assert "高材制造" in prompt
        assert "industry" in prompt


class TestExplorerAgentHarness:
    def test_harness_limits(self):
        agent = ExplorerAgent(section="industry", category="高材制造")
        harness = agent._build_harness()
        assert harness.max_steps == 25
        assert harness.max_duration_seconds == 300.0
