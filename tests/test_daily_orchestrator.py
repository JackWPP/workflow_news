from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.daily_orchestrator import CATEGORIES, DailyOrchestrator


def _candidate(
    *,
    title: str = "Test Article",
    url: str = "https://example.com/1",
    domain: str = "example.com",
    category: str = "塑料",
) -> dict:
    return {
        "title": title,
        "url": url,
        "domain": domain,
        "source_name": domain,
        "summary": "test summary",
        "key_finding": "test finding",
        "source_tier": "A",
        "source_kind": "industry_media",
        "why_selected": "test reason",
        "image_url": None,
        "published_at": None,
    }


def _card(
    *,
    title: str = "Test Article",
    url: str = "https://example.com/1",
    section: str = "industry",
    category: str = "塑料",
) -> dict:
    return {
        "title": title,
        "url": url,
        "domain": "example.com",
        "section": section,
        "category": category,
        "summary": "test summary",
        "key_finding": "test finding",
        "editor_notes": "test notes",
        "status": "approved",
        "rank": 1,
    }


class TestCategories:
    def test_categories_has_three_entries(self):
        assert len(CATEGORIES) == 3

    def test_categories_cover_expected_materials(self):
        assert set(CATEGORIES) == {"塑料", "橡胶", "纤维"}


class TestOrchestratorInit:
    def test_init_with_default_llm(self):
        orchestrator = DailyOrchestrator()
        assert orchestrator._llm is not None

    def test_init_with_custom_llm(self):
        mock_llm = SimpleNamespace()
        orchestrator = DailyOrchestrator(llm_client=mock_llm)
        assert orchestrator._llm is mock_llm


class TestParallelExplore:
    @pytest.mark.asyncio
    async def test_explore_runs_three_explorers_in_parallel(self):
        explore_calls = []

        async def mock_explore(self, run_id=None):
            explore_calls.append(self.category)
            return [_candidate(category=self.category)]

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore,
        ):
            results = await orchestrator._phase_explore(None, None)

        assert len(results) == 3
        assert set(explore_calls) == {"塑料", "橡胶", "纤维"}

    @pytest.mark.asyncio
    async def test_explore_returns_empty_on_exception(self):
        async def mock_explore_fail(self, run_id=None):
            raise RuntimeError("search failed")

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore_fail,
        ):
            results = await orchestrator._phase_explore(None, None)

        assert len(results) == 3
        assert all(r == [] for r in results)

    @pytest.mark.asyncio
    async def test_explore_sends_progress_events(self):
        queue = asyncio.Queue()

        async def mock_explore(self, run_id=None):
            return [_candidate()]

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore,
        ):
            await orchestrator._phase_explore(None, queue)

        events = []
        while not queue.empty():
            events.append(await queue.get())

        phase_events = [e for e in events if e.get("type") == "phase"]
        progress_events = [e for e in events if e.get("type") == "progress"]

        assert len(phase_events) == 1
        assert phase_events[0]["phase"] == "explore"
        assert len(progress_events) == 3


class TestParallelEdit:
    @pytest.mark.asyncio
    async def test_edit_runs_three_editors_in_parallel(self):
        edit_calls = []

        async def mock_edit(self, candidates, run_id=None):
            edit_calls.append(self.category)
            return [_card(category=self.category)]

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())
        explore_results = [[_candidate()] for _ in CATEGORIES]

        with patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            results = await orchestrator._phase_edit(explore_results, None, None)

        assert len(results) == 3
        assert set(edit_calls) == {"塑料", "橡胶", "纤维"}

    @pytest.mark.asyncio
    async def test_edit_returns_empty_on_exception(self):
        async def mock_edit_fail(self, candidates, run_id=None):
            raise RuntimeError("edit failed")

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())
        explore_results = [[_candidate()] for _ in CATEGORIES]

        with patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit_fail,
        ):
            results = await orchestrator._phase_edit(explore_results, None, None)

        assert len(results) == 3
        assert all(r == [] for r in results)

    @pytest.mark.asyncio
    async def test_edit_sends_progress_events(self):
        queue = asyncio.Queue()

        async def mock_edit(self, candidates, run_id=None):
            return [_card()]

        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())
        explore_results = [[_candidate()] for _ in CATEGORIES]

        with patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            await orchestrator._phase_edit(explore_results, None, queue)

        events = []
        while not queue.empty():
            events.append(await queue.get())

        phase_events = [e for e in events if e.get("type") == "phase"]
        progress_events = [e for e in events if e.get("type") == "progress"]

        assert len(phase_events) == 1
        assert phase_events[0]["phase"] == "edit"
        assert len(progress_events) == 3


class TestPhaseSummary:
    @pytest.mark.asyncio
    async def test_summary_with_empty_cards(self):
        orchestrator = DailyOrchestrator(llm_client=SimpleNamespace())
        result = await orchestrator._phase_summary([[], [], []], None, None)

        assert "无可用" in result["summary"]
        assert result["html"] == ""

    @pytest.mark.asyncio
    async def test_summary_generates_html(self):
        orchestrator = DailyOrchestrator(llm_client=None)
        edit_results = [[_card(title=f"Article {i}")] for i in range(3)]
        result = await orchestrator._phase_summary(edit_results, None, None)

        assert result["html"] != ""
        assert "Article" in result["html"]


class TestFullRun:
    @pytest.mark.asyncio
    async def test_run_returns_all_keys(self):
        async def mock_explore(self, run_id=None):
            return [_candidate(category=self.category)]

        async def mock_edit(self, candidates, run_id=None):
            return [_card(category=self.category)]

        orchestrator = DailyOrchestrator(llm_client=None)

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore,
        ), patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            result = await orchestrator.run(run_id=1)

        assert "html" in result
        assert "summary" in result
        assert "foresight" in result
        assert "trends" in result
        assert "follow_up" in result
        assert "meta" in result

    @pytest.mark.asyncio
    async def test_run_meta_contains_expected_fields(self):
        async def mock_explore(self, run_id=None):
            return [_candidate(category=self.category)]

        async def mock_edit(self, candidates, run_id=None):
            return [_card(category=self.category)]

        orchestrator = DailyOrchestrator(llm_client=None)

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore,
        ), patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            result = await orchestrator.run()

        meta = result["meta"]
        assert "total_cards" in meta
        assert "elapsed_seconds" in meta
        assert "categories" in meta
        assert meta["total_cards"] == 3

    @pytest.mark.asyncio
    async def test_run_sends_all_phase_events(self):
        queue = asyncio.Queue()

        async def mock_explore(self, run_id=None):
            return [_candidate()]

        async def mock_edit(self, candidates, run_id=None):
            return [_card()]

        orchestrator = DailyOrchestrator(llm_client=None)

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore,
        ), patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            await orchestrator.run(event_queue=queue)

        events = []
        while not queue.empty():
            events.append(await queue.get())

        phase_types = [e["phase"] for e in events if e.get("type") == "phase"]
        assert "explore" in phase_types
        assert "edit" in phase_types
        assert "summary" in phase_types

    @pytest.mark.asyncio
    async def test_run_handles_explorer_failure_gracefully(self):
        async def mock_explore_fail(self, run_id=None):
            raise RuntimeError("boom")

        async def mock_edit(self, candidates, run_id=None):
            return [_card(category=self.category)] if candidates else []

        orchestrator = DailyOrchestrator(llm_client=None)

        with patch(
            "app.services.daily_orchestrator.ExplorerAgent.explore",
            new=mock_explore_fail,
        ), patch(
            "app.services.daily_orchestrator.SectionEditorAgent.edit",
            new=mock_edit,
        ):
            result = await orchestrator.run()

        assert "meta" in result
        assert result["meta"]["total_cards"] == 0
