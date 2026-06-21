from __future__ import annotations

import pytest

from app.services.summary_agent import SummaryAgent


def _card(
    *,
    title: str = "测试文章",
    url: str = "https://example.com/1",
    domain: str = "example.com",
    section: str = "industry",
    category: str = "塑料",
    summary: str = "这是一篇关于高分子材料的测试文章摘要",
    key_finding: str = "新型聚乳酸材料突破",
    evaluation_reason: str = "与高分子加工高度相关",
    image_url: str | None = None,
    keywords: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "domain": domain,
        "section": section,
        "category": category,
        "summary": summary,
        "key_finding": key_finding,
        "evaluation_reason": evaluation_reason,
        "image_url": image_url,
        "keywords": keywords or ["聚乳酸", "PLA"],
    }


def _make_cards() -> list[dict]:
    return [
        _card(
            title="新型注塑设备发布",
            section="industry",
            category="塑料",
            key_finding="某企业发布新型注塑机",
        ),
        _card(
            title="聚乳酸降解新进展",
            section="academic",
            category="塑料",
            key_finding="PLA降解效率提升30%",
            keywords=["聚乳酸", "降解", "PLA"],
        ),
        _card(
            title="轮胎产能扩建",
            section="industry",
            category="橡胶",
            key_finding="某企业新建轮胎生产线",
        ),
    ]


class TestGroupBySection:
    def test_groups_correctly(self):
        agent = SummaryAgent(llm_client=None)
        cards = _make_cards()
        grouped = agent._group_by_section(cards)

        assert "塑料" in grouped
        assert "橡胶" in grouped
        assert len(grouped["塑料"]) == 2
        assert len(grouped["橡胶"]) == 1

    def test_default_category_is_plastic(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(category=None)]
        grouped = agent._group_by_section(cards)

        assert "塑料" in grouped
        assert len(grouped["塑料"]) == 1

    def test_empty_cards(self):
        agent = SummaryAgent(llm_client=None)
        grouped = agent._group_by_section([])
        assert grouped == {}


class TestHeuristicAnalysis:
    def test_returns_expected_keys(self):
        grouped = {
            "塑料": [_card(category="塑料"), _card(category="塑料")],
            "橡胶": [_card(category="橡胶")],
        }
        result = SummaryAgent._heuristic_analysis(grouped)

        assert "summary" in result
        assert "foresight" in result
        assert "trends" in result
        assert "follow_up" in result
        assert isinstance(result["trends"], list)
        assert isinstance(result["follow_up"], list)

    def test_mentions_article_count(self):
        grouped = {"塑料": [_card(), _card()], "橡胶": [_card()]}
        result = SummaryAgent._heuristic_analysis(grouped)

        assert "3" in result["summary"]

    def test_plastic_trend_included(self):
        grouped = {
            "塑料": [_card(), _card(), _card()],
        }
        result = SummaryAgent._heuristic_analysis(grouped)

        assert any("塑料" in t for t in result["trends"])


class TestGenerate:
    @pytest.mark.asyncio
    async def test_empty_cards_returns_empty_result(self):
        agent = SummaryAgent(llm_client=None)
        result = await agent.generate([])

        assert result["html"] == ""
        assert "无可用" in result["summary"]
        assert result["trends"] == []

    @pytest.mark.asyncio
    async def test_generate_returns_all_keys(self):
        agent = SummaryAgent(llm_client=None)
        cards = _make_cards()
        result = await agent.generate(cards)

        assert "html" in result
        assert "summary" in result
        assert "foresight" in result
        assert "trends" in result
        assert "follow_up" in result

    @pytest.mark.asyncio
    async def test_html_contains_category_nav(self):
        agent = SummaryAgent(llm_client=None)
        cards = _make_cards()
        result = await agent.generate(cards)

        html = result["html"]
        assert "section-塑料" in html
        assert "section-橡胶" in html

    @pytest.mark.asyncio
    async def test_html_contains_card_titles(self):
        agent = SummaryAgent(llm_client=None)
        cards = _make_cards()
        result = await agent.generate(cards)

        html = result["html"]
        assert "新型注塑设备发布" in html
        assert "聚乳酸降解新进展" in html
        assert "轮胎产能扩建" in html

    @pytest.mark.asyncio
    async def test_html_contains_summary_and_trends(self):
        agent = SummaryAgent(llm_client=None)
        cards = _make_cards()
        result = await agent.generate(cards)

        html = result["html"]
        assert "今日总结" in html
        assert "前瞻洞察" in html
        assert "趋势判断" in html
        assert "后续追踪" in html

    @pytest.mark.asyncio
    async def test_html_contains_images_when_present(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(image_url="https://example.com/img.jpg")]
        result = await agent.generate(cards)

        assert "https://example.com/img.jpg" in result["html"]
        assert "<img" in result["html"]

    @pytest.mark.asyncio
    async def test_html_no_images_when_absent(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(image_url=None)]
        result = await agent.generate(cards)

        assert "<img" not in result["html"]

    @pytest.mark.asyncio
    async def test_html_contains_keywords(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(keywords=["聚乙烯", "PE", "注塑"])]
        result = await agent.generate(cards)

        html = result["html"]
        assert "聚乙烯" in html
        assert "PE" in html

    @pytest.mark.asyncio
    async def test_html_contains_category_badge(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(category="橡胶")]
        result = await agent.generate(cards)

        assert "橡胶" in result["html"]
        assert "category-badge" in result["html"]


class TestRenderCards:
    def test_render_single_card(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(title="测试标题", key_finding="核心发现")]
        html = agent._render_cards(cards)

        assert "测试标题" in html
        assert "核心发现" in html
        assert "card" in html

    def test_render_card_with_reason(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(evaluation_reason="与行业高度相关")]
        html = agent._render_cards(cards)

        assert "与行业高度相关" in html
        assert "card-reason" in html

    def test_render_card_without_reason(self):
        agent = SummaryAgent(llm_client=None)
        cards = [_card(evaluation_reason="")]
        html = agent._render_cards(cards)

        assert "card-reason" not in html
