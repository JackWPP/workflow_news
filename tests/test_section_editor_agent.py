from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.section_editor_agent import (
    _TITLE_SIMILARITY_THRESHOLD,
    SectionEditorAgent,
    _keyword_score,
    _title_similarity,
    deduplicate_candidates,
    rank_candidates,
)


def _make_candidate(
    url: str = "https://example.com/article1",
    title: str = "Test Article",
    domain: str = "example.com",
    source_tier: str = "B",
    source_kind: str = "general_site",
    published_at: str | None = "2026-06-13T10:00:00Z",
    summary: str = "",
    key_finding: str = "",
    why_selected: str = "",
    image_url: str | None = None,
    section: str = "industry",
) -> dict:
    return {
        "url": url,
        "title": title,
        "domain": domain,
        "source_name": domain,
        "summary": summary,
        "key_finding": key_finding,
        "source_tier": source_tier,
        "source_kind": source_kind,
        "why_selected": why_selected,
        "image_url": image_url,
        "published_at": published_at,
        "section": section,
    }


# ── Title Similarity ──────────────────────────────────────


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert _title_similarity("Hello World", "Hello World") == 1.0

    def test_similar_titles(self):
        sim = _title_similarity(
            "新型注塑机技术发布",
            "新型注塑机技术正式发布",
        )
        assert sim >= _TITLE_SIMILARITY_THRESHOLD

    def test_different_titles(self):
        sim = _title_similarity(
            "注塑机新品发布",
            "碳关税政策解读",
        )
        assert sim < 0.5

    def test_empty_strings(self):
        assert _title_similarity("", "") == 1.0

    def test_case_insensitive(self):
        assert _title_similarity("Hello World", "hello world") == 1.0


# ── Keyword Score ──────────────────────────────────────────


class TestKeywordScore:
    def test_plastic_category_match(self):
        text = "新型注塑机采用聚乙烯薄膜"
        score = _keyword_score(text, "塑料")
        assert score > 0.05

    def test_rubber_category_match(self):
        text = "轮胎橡胶密封件弹性体TPU"
        score = _keyword_score(text, "橡胶")
        assert score > 0.1

    def test_fiber_category_match(self):
        text = "碳纤维芳纶涤纶纺丝工艺"
        score = _keyword_score(text, "纤维")
        assert score > 0.1

    def test_no_match(self):
        text = "今天天气不错"
        score = _keyword_score(text, "塑料")
        assert score == 0.0

    def test_unknown_category(self):
        score = _keyword_score("test", "UnknownCategory")
        assert score == 0.0


# ── Deduplication ──────────────────────────────────────────


class TestDeduplication:
    def test_removes_exact_url_duplicates(self):
        candidates = [
            _make_candidate(url="https://a.com/1", title="注塑机新品发布"),
            _make_candidate(url="https://a.com/1", title="碳关税政策解读"),
            _make_candidate(url="https://b.com/2", title="学术论文发表"),
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 2
        urls = {c["url"] for c in result}
        assert "https://a.com/1" in urls
        assert "https://b.com/2" in urls

    def test_removes_similar_title_duplicates(self):
        candidates = [
            _make_candidate(
                url="https://a.com/1",
                title="新型注塑机技术发布",
                source_tier="C",
            ),
            _make_candidate(
                url="https://b.com/2",
                title="新型注塑机技术正式发布",
                source_tier="A",
            ),
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 1
        assert result[0]["source_tier"] == "A"

    def test_keeps_different_articles(self):
        candidates = [
            _make_candidate(url="https://a.com/1", title="注塑机新闻"),
            _make_candidate(url="https://b.com/2", title="碳关税政策"),
            _make_candidate(url="https://c.com/3", title="学术论文发表"),
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 3

    def test_empty_input(self):
        assert deduplicate_candidates([]) == []

    def test_skips_candidates_without_url(self):
        candidates = [
            _make_candidate(url="", title="No URL"),
            _make_candidate(url="https://a.com/1", title="Has URL"),
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 1

    def test_prefers_higher_tier_on_similar_titles(self):
        candidates = [
            _make_candidate(
                url="https://low.com/1",
                title="Polymer processing breakthrough in 2026",
                source_tier="C",
            ),
            _make_candidate(
                url="https://high.com/2",
                title="Polymer processing breakthrough in 2026 announced",
                source_tier="A",
            ),
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 1
        assert result[0]["source_tier"] == "A"


# ── Ranking ────────────────────────────────────────────────


class TestRanking:
    def test_higher_tier_ranks_first(self):
        candidates = [
            _make_candidate(url="https://c.com/1", source_tier="C"),
            _make_candidate(url="https://a.com/2", source_tier="A"),
            _make_candidate(url="https://b.com/3", source_tier="B"),
        ]
        result = rank_candidates(candidates)
        assert result[0]["source_tier"] == "A"
        assert result[-1]["source_tier"] == "C"

    def test_keyword_relevant_ranks_higher(self):
        candidates = [
            _make_candidate(
                url="https://a.com/1",
                title="Unrelated news",
                summary="Nothing about polymers",
                source_tier="B",
            ),
            _make_candidate(
                url="https://b.com/2",
                title="注塑机新品发布",
                summary="聚乙烯薄膜应用",
                source_tier="B",
            ),
        ]
        result = rank_candidates(candidates, category="塑料")
        assert result[0]["url"] == "https://b.com/2"

    def test_empty_input(self):
        assert rank_candidates([]) == []

    def test_no_published_at_gets_low_freshness(self):
        candidates = [
            _make_candidate(
                url="https://a.com/1",
                published_at=None,
                source_tier="A",
            ),
            _make_candidate(
                url="https://b.com/2",
                published_at="2026-06-19T10:00:00Z",
                source_tier="A",
            ),
        ]
        result = rank_candidates(candidates)
        assert result[0]["url"] == "https://b.com/2"


# ── SectionEditorAgent Init ───────────────────────────────


class TestSectionEditorAgentInit:
    def test_init_with_defaults(self):
        agent = SectionEditorAgent(category="塑料")
        assert agent.category == "塑料"
        assert agent._llm is not None

    def test_init_with_custom_llm(self):
        mock_llm = SimpleNamespace()
        agent = SectionEditorAgent(
            category="橡胶",
            llm_client=mock_llm,
        )
        assert agent._llm is mock_llm

    def test_harness_limits(self):
        agent = SectionEditorAgent(category="塑料")
        harness = agent._build_harness()
        assert harness.max_steps == 20
        assert harness.max_duration_seconds == 240.0

    def test_tools_include_expected(self):
        agent = SectionEditorAgent(category="塑料")
        tools = agent._build_tools()
        tool_names = {t.name for t in tools}
        assert "evaluate_article" in tool_names
        assert "compare_sources" in tool_names
        assert "write_section" in tool_names
        assert "check_coverage" in tool_names
        assert "finish" in tool_names

    def test_tools_exclude_search(self):
        agent = SectionEditorAgent(category="塑料")
        tools = agent._build_tools()
        tool_names = {t.name for t in tools}
        assert "web_search" not in tool_names
        assert "read_page" not in tool_names

    def test_task_prompt_contains_category(self):
        agent = SectionEditorAgent(category="纤维")
        candidates = [_make_candidate()]
        prompt = agent._build_task_prompt(candidates)
        assert "纤维" in prompt

    def test_task_prompt_lists_candidates(self):
        agent = SectionEditorAgent(category="塑料")
        candidates = [
            _make_candidate(title="Article One", domain="a.com"),
            _make_candidate(title="Article Two", domain="b.com"),
        ]
        prompt = agent._build_task_prompt(candidates)
        assert "Article One" in prompt
        assert "Article Two" in prompt


# ── Card Output ────────────────────────────────────────────


class TestCardOutput:
    def test_card_has_all_required_fields(self):
        agent = SectionEditorAgent(category="塑料")
        ranked = [_make_candidate()]
        result = SimpleNamespace(
            success=True,
            finished_reason="finish_tool",
            articles=[],
            sections_content={},
            memory_snapshot={},
            harness_status={},
            step_count=5,
            total_tokens=1000,
            diagnostics={},
        )
        from app.services.working_memory import WorkingMemory
        memory = WorkingMemory()

        cards = agent._build_cards(ranked, result, memory)
        assert len(cards) == 1
        card = cards[0]
        for field in [
            "title", "url", "domain", "source_tier", "rank",
            "status", "editor_notes", "section", "category",
        ]:
            assert field in card, f"Missing field: {field}"

    def test_card_rank_is_sequential(self):
        agent = SectionEditorAgent(category="塑料")
        ranked = [
            _make_candidate(url=f"https://a.com/{i}", title=f"Article {i}")
            for i in range(5)
        ]
        result = SimpleNamespace(
            success=True,
            finished_reason="finish_tool",
            articles=[],
            sections_content={},
        )
        from app.services.working_memory import WorkingMemory
        memory = WorkingMemory()

        cards = agent._build_cards(ranked, result, memory)
        for i, card in enumerate(cards):
            assert card["rank"] == i + 1

    def test_card_section_from_candidate_and_category_match_agent(self):
        agent = SectionEditorAgent(category="橡胶")
        ranked = [_make_candidate(section="policy")]
        result = SimpleNamespace(
            success=True,
            finished_reason="finish_tool",
            articles=[],
            sections_content={},
        )
        from app.services.working_memory import WorkingMemory
        memory = WorkingMemory()

        cards = agent._build_cards(ranked, result, memory)
        assert cards[0]["section"] == "policy"
        assert cards[0]["category"] == "橡胶"

    def test_empty_candidates_returns_empty(self):
        import asyncio

        async def run():
            agent = SectionEditorAgent(category="塑料")
            return await agent.edit([])

        cards = asyncio.get_event_loop().run_until_complete(run())
        assert cards == []


# ── Edit Integration ───────────────────────────────────────


class TestEditIntegration:
    def test_edit_deduplicates_before_ranking(self):
        candidates = [
            _make_candidate(
                url="https://a.com/1",
                title="Duplicate article",
                source_tier="C",
            ),
            _make_candidate(
                url="https://a.com/1",
                title="Duplicate article copy",
                source_tier="C",
            ),
            _make_candidate(
                url="https://b.com/2",
                title="Unique article",
                source_tier="A",
            ),
        ]
        deduped = deduplicate_candidates(candidates)
        assert len(deduped) == 2
        ranked = rank_candidates(deduped, "塑料")
        assert ranked[0]["url"] == "https://b.com/2"
