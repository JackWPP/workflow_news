from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.editor_agent import EditorAgent


def _article(
    *,
    idx: int,
    domain: str,
    title: str,
    summary: str = "polymer plastics materials processing",
    source_type: str = "template_search",
    section: str | None = None,
    category: str | None = None,
    metadata: dict | None = None,
):
    return SimpleNamespace(
        id=idx,
        url=f"https://{domain}/news/{idx}",
        title=title,
        domain=domain,
        source_type=source_type,
        language="zh",
        raw_content="polymer processing " * 40,
        summary=summary,
        published_at=datetime(2026, 6, 13, tzinfo=UTC),
        ingested_at=datetime(2026, 6, 13, tzinfo=UTC),
        quality_score=None,
        section=section,
        category=category,
        eval_metadata=metadata or {},
    )


class TestEditorAgentSeedRanking:
    def test_filters_marketing_domains_and_caps_single_domain(self):
        articles = [
            _article(idx=1, domain="cir.cn", title="2026年聚乳酸杯发展趋势分析"),
            _article(idx=2, domain="nature.com", title="Polymer materials study", source_type="rss", section="academic"),
            _article(idx=3, domain="miit.gov.cn", title="塑料回收政策通知", section="policy"),
            _article(idx=4, domain="example.com", title="注塑设备新品发布", section="industry"),
            _article(idx=5, domain="example.com", title="挤出设备技术升级", section="industry"),
            _article(idx=6, domain="example.com", title="吹塑设备技术升级", section="industry"),
            _article(idx=9, domain="mysteel.com", title="塑料收盘价格表(20260612)"),
            _article(
                idx=10,
                domain="arxiv.org",
                title="Charting quantum materials manifold",
                summary="quantum materials platform and magnetic states",
            ),
            _article(
                idx=11,
                domain="gr.xjtu.edu.cn",
                title="张淼 西安交通大学教师主页管理系统 研究领域",
                summary="导电高分子 储能",
            ),
            _article(
                idx=7,
                domain="mit.edu",
                title="AI polymer processing digital twin",
                section="academic",
                category="AI",
                metadata={"discovery": {"intended_category": "AI", "query_family": "ai_materials"}},
            ),
        ]

        selected = EditorAgent._rank_and_balance_seed_articles(articles, limit=10)
        domains = [article.domain for article in selected]
        titles = [article.title for article in selected]

        assert "cir.cn" not in domains
        assert "塑料收盘价格表(20260612)" not in titles
        assert "Charting quantum materials manifold" not in titles
        assert "张淼 西安交通大学教师主页管理系统 研究领域" not in titles
        assert domains.count("example.com") <= 2
        assert "Polymer materials study" in titles
        assert "塑料回收政策通知" in titles
        assert "AI polymer processing digital twin" in titles

    def test_article_to_seed_preserves_category_and_source_quality(self):
        article = _article(
            idx=8,
            domain="mit.edu",
            title="AI polymer processing digital twin",
            section="academic",
            category="AI",
        )

        seed = EditorAgent._article_to_seed(article)

        assert seed["section"] == "academic"
        assert seed["category"] == "AI"
        assert seed["source_tier"] in {"A", "B"}

    def test_section_minimum_quota_fills_policy(self):
        articles = [
            _article(idx=1, domain="a.com", title="Industry 1", section="industry", category="高材制造"),
            _article(idx=2, domain="b.com", title="Industry 2", section="industry", category="高材制造"),
            _article(idx=3, domain="c.com", title="Industry 3", section="industry", category="清洁能源"),
            _article(idx=4, domain="d.com", title="Industry 4", section="industry", category="AI"),
            _article(idx=5, domain="e.com", title="Academic 1", section="academic", category="高材制造"),
            _article(idx=6, domain="f.com", title="Academic 2", section="academic", category="清洁能源"),
            _article(idx=7, domain="g.com", title="Policy 1", section="policy", category="清洁能源"),
            _article(idx=8, domain="h.com", title="Policy 2", section="policy", category="AI"),
        ]

        selected = EditorAgent._rank_and_balance_seed_articles(articles, limit=8)
        sections = [a.section for a in selected]

        assert sections.count("policy") >= 2
        assert sections.count("industry") >= 2
        assert sections.count("academic") >= 2

    def test_section_minimum_quota_skipped_when_insufficient_candidates(self):
        articles = [
            _article(idx=1, domain="a.com", title="Industry 1", section="industry", category="高材制造"),
            _article(idx=2, domain="b.com", title="Industry 2", section="industry", category="清洁能源"),
            _article(idx=3, domain="c.com", title="Academic 1", section="academic", category="高材制造"),
        ]

        selected = EditorAgent._rank_and_balance_seed_articles(articles, limit=10)

        assert len(selected) == 3
