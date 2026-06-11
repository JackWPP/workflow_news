from __future__ import annotations

import pytest

from app.services.working_memory import (
    ArticleSummary,
    CoverageState,
    ExplorationLead,
    ImageCandidate,
    StepRecord,
    WorkingMemory,
)


def _make_article(**overrides) -> ArticleSummary:
    defaults = dict(
        title="Test Article",
        url="https://example.com/a1",
        domain="example.com",
        source_name="TestSource",
        published_at="2026-01-15",
        summary="Test summary",
        section="industry",
        key_finding="Test finding",
        worth_publishing=True,
    )
    defaults.update(overrides)
    return ArticleSummary(**defaults)


class TestWorkingMemoryRecordRead:
    def test_record_read(self):
        mem = WorkingMemory()
        mem.record_read("https://example.com/page1")
        assert mem.has_read("https://example.com/page1") is True

    def test_has_read_false(self):
        mem = WorkingMemory()
        assert mem.has_read("https://example.com/page1") is False

    def test_record_read_with_links(self):
        mem = WorkingMemory()
        links = [{"url": "https://example.com/linked", "text": "Link"}]
        mem.record_read("https://example.com/page1", links=links)
        assert len(mem.get_page_links("https://example.com/page1")) == 1

    def test_record_page_attempt_non_readable(self):
        mem = WorkingMemory()
        mem.record_page_attempt("https://example.com/blocked", "anti_bot")
        assert mem.has_attempted_read("https://example.com/blocked") is True
        assert mem.has_read("https://example.com/blocked") is False


class TestWorkingMemoryAddArticle:
    def test_add_article(self):
        mem = WorkingMemory()
        article = _make_article()
        mem.add_article(article)
        assert len(mem.discovered_articles) == 1

    def test_add_duplicate_url_skipped(self):
        mem = WorkingMemory()
        mem.add_article(_make_article(url="https://example.com/a1"))
        mem.add_article(_make_article(url="https://example.com/a1", title="Different"))
        assert len(mem.discovered_articles) == 1

    def test_publishable_articles(self):
        mem = WorkingMemory()
        mem.add_article(_make_article(worth_publishing=True))
        mem.add_article(_make_article(url="https://example.com/a2", worth_publishing=False))
        assert len(mem.publishable_articles()) == 1


class TestWorkingMemoryHasSearched:
    def test_has_searched_false_initially(self):
        mem = WorkingMemory()
        assert mem.has_searched("polymer") is False

    def test_record_search_then_found(self):
        mem = WorkingMemory()
        mem.record_search("polymer recycling")
        assert mem.has_searched("polymer recycling") is True

    def test_case_insensitive_search(self):
        mem = WorkingMemory()
        mem.record_search("Polymer Recycling")
        assert mem.has_searched("polymer recycling") is True


class TestWorkingMemoryCoverageState:
    def test_coverage_updated_on_add(self):
        mem = WorkingMemory()
        mem.add_article(_make_article(section="industry"))
        mem.add_article(_make_article(url="https://e.com/a2", section="academic"))
        mem.add_article(_make_article(url="https://e.com/a3", section="policy"))
        assert mem.coverage.section_count == 3
        assert mem.coverage.total_articles == 3

    def test_is_publishable(self):
        cs = CoverageState(academic_count=2, industry_count=2, policy_count=1)
        assert cs.is_publishable is True

    def test_is_not_publishable_insufficient(self):
        cs = CoverageState(academic_count=1, industry_count=0, policy_count=0)
        assert cs.is_publishable is False

    def test_is_complete(self):
        cs = CoverageState(academic_count=2, industry_count=2, policy_count=2)
        assert cs.is_complete is True

    def test_gaps_reported(self):
        cs = CoverageState(academic_count=0, industry_count=1, policy_count=0)
        gaps = cs.gaps()
        assert len(gaps) > 0

    def test_rebuild_coverage(self):
        mem = WorkingMemory()
        mem.add_article(_make_article(section="industry"))
        mem.add_article(_make_article(url="https://e.com/a2", section="industry"))
        mem.coverage.academic_count = 99
        mem.rebuild_coverage()
        assert mem.coverage.academic_count == 0
        assert mem.coverage.industry_count == 2

    def test_formal_topic_count(self):
        mem = WorkingMemory()
        mem.set_formal_topic_count(5)
        assert mem.coverage.formal_topic_count == 5


class TestWorkingMemoryToContextSummary:
    def test_empty_memory_summary(self):
        mem = WorkingMemory()
        summary = mem.to_context_summary()
        assert "广度搜索" in summary

    def test_summary_with_queries(self):
        mem = WorkingMemory()
        mem.record_search("polymer recycling")
        mem.record_search("injection molding")
        summary = mem.to_context_summary()
        assert "已搜索" in summary

    def test_summary_with_articles(self):
        mem = WorkingMemory()
        mem.add_article(_make_article(section="industry"))
        summary = mem.to_context_summary()
        assert "已确认" in summary


class TestWorkingMemoryStepRecording:
    def test_record_step(self):
        mem = WorkingMemory()
        step = StepRecord(
            step_index=0,
            tool_name="web_search",
            arguments={"query": "test"},
            result_summary="Found 5 results",
            duration_seconds=1.5,
        )
        mem.record_step(step)
        assert len(mem.step_history) == 1

    def test_record_thought(self):
        mem = WorkingMemory()
        mem.record_thought("I should search for polymer news")
        assert len(mem.thoughts) == 1


class TestWorkingMemoryExploration:
    def test_add_lead(self):
        mem = WorkingMemory()
        lead = ExplorationLead(url="https://example.com/new", title="New", reason="Related", priority=0.8)
        mem.add_exploration_lead(lead)
        assert len(mem.exploration_queue) == 1

    def test_pop_best_lead(self):
        mem = WorkingMemory()
        mem.add_exploration_lead(ExplorationLead(url="https://e.com/a", title="A", reason="", priority=0.5))
        mem.add_exploration_lead(ExplorationLead(url="https://e.com/b", title="B", reason="", priority=0.9))
        best = mem.pop_best_lead()
        assert best.url == "https://e.com/b"

    def test_pop_empty_returns_none(self):
        mem = WorkingMemory()
        assert mem.pop_best_lead() is None


class TestWorkingMemorySnapshot:
    def test_snapshot_structure(self):
        mem = WorkingMemory()
        mem.record_search("test")
        mem.add_article(_make_article())
        snap = mem.snapshot()
        assert "searched_queries" in snap
        assert "coverage" in snap
        assert "articles" in snap

    def test_to_json(self):
        mem = WorkingMemory()
        j = mem.to_json()
        assert isinstance(j, str)
        assert "searched_queries" in j
