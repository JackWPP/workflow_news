"""
working_memory.py — Agent 的工作记忆

在一次 Agent 运行中，WorkingMemory 记录了：
  - 已搜索的 query（避免重复）
  - 已阅读的 URL（避免重复）
  - 已发现的文章（按主题分组）
  - 待探索的线索（从已读页面发现的新方向）
  - 已拒绝的方向（记住为什么放弃）
  - 关键发现（跨文章的主题洞察）
  - 图片候选
  - 当前覆盖状态

WorkingMemory 是 Agent 的认知状态——它让 Agent 知道
"我已经知道了什么"、"我还需要探索什么"。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.utils import canonicalize_url, normalize_external_url


@dataclass
class ArticleSummary:
    """Agent 发现的一篇文章的摘要信息。"""

    title: str
    url: str
    domain: str
    source_name: str
    published_at: str | None
    summary: str
    section: str  # academic / industry / policy
    key_finding: str  # Agent 从这篇文章提炼的核心洞察
    has_image: bool = False
    image_url: str | None = None
    worth_publishing: bool = True
    evaluation_reason: str = ""
    search_query: str = ""
    resolved_url: str | None = None
    source_tier: str = "C"
    source_reliability_label: str = "中（仅可辅助参考）"
    source_kind: str = "general_site"
    page_kind: str = "article"
    evidence_strength: str = "low"
    supports_numeric_claims: bool = False
    allowed_for_trend_summary: bool = False
    is_primary_source: bool = False
    requires_observation_only: bool = False
    category: str = ""  # 高材制造 / 清洁能源 / AI
    selection_reason: str = ""
    topic_confidence: str = ""
    excluded_reason: str = ""
    recency_status: str = "unknown"
    published_at_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "summary": self.summary,
            "section": self.section,
            "key_finding": self.key_finding,
            "has_image": self.has_image,
            "image_url": self.image_url,
            "worth_publishing": self.worth_publishing,
            "evaluation_reason": self.evaluation_reason,
            "search_query": self.search_query,
            "resolved_url": self.resolved_url,
            "source_tier": self.source_tier,
            "source_reliability_label": self.source_reliability_label,
            "source_kind": self.source_kind,
            "page_kind": self.page_kind,
            "evidence_strength": self.evidence_strength,
            "supports_numeric_claims": self.supports_numeric_claims,
            "allowed_for_trend_summary": self.allowed_for_trend_summary,
            "is_primary_source": self.is_primary_source,
            "requires_observation_only": self.requires_observation_only,
            "category": self.category,
            "selection_reason": self.selection_reason,
            "topic_confidence": self.topic_confidence,
            "excluded_reason": self.excluded_reason,
            "recency_status": self.recency_status,
            "published_at_source": self.published_at_source,
        }


@dataclass
class ImageCandidate:
    """Agent 找到的图片候选。"""

    image_url: str
    source_url: str
    caption: str
    relevance_score: float  # 0-1
    origin_type: str  # article_inline / search_result / og_image
    verified: bool = False
    verification_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_url": self.image_url,
            "source_url": self.source_url,
            "caption": self.caption,
            "relevance_score": self.relevance_score,
            "origin_type": self.origin_type,
            "verified": self.verified,
            "verification_reason": self.verification_reason,
        }


@dataclass
class ExplorationLead:
    """从已读内容中发现的值得探索的新线索。"""

    url: str
    title: str
    reason: str  # 为什么值得探索
    priority: float  # 0-1，越高越优先


@dataclass
class CoverageState:
    """当前覆盖状态检查。"""

    academic_count: int = 0
    industry_count: int = 0
    policy_count: int = 0
    image_count: int = 0
    verified_image_count: int = 0
    formal_topic_count: int = 0

    @property
    def section_count(self) -> int:
        return sum(
            [
                1 if self.academic_count > 0 else 0,
                1 if self.industry_count > 0 else 0,
                1 if self.policy_count > 0 else 0,
            ]
        )

    @property
    def total_articles(self) -> int:
        return self.academic_count + self.industry_count + self.policy_count

    @property
    def is_publishable(self) -> bool:
        topic_count = self.formal_topic_count or self.total_articles
        return topic_count >= 4 and self.section_count >= 2

    @property
    def is_complete(self) -> bool:
        topic_count = self.formal_topic_count or self.total_articles
        return topic_count >= 6 and self.section_count >= 2

    def gaps(self) -> list[str]:
        gaps = []
        topic_count = self.formal_topic_count or self.total_articles
        if topic_count < 4:
            label = "正式主题" if self.formal_topic_count else "高质量条目"
            gaps.append(f"{label}不足（{topic_count}/4）")
        if self.section_count < 2:
            missing = []
            if self.industry_count == 0:
                missing.append("产业动态")
            if self.policy_count == 0:
                missing.append("政策标准")
            if self.academic_count == 0:
                missing.append("学术前沿")
            gaps.append(f"板块不足，缺少：{', '.join(missing[:2])}")
        return gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "academic_count": self.academic_count,
            "industry_count": self.industry_count,
            "policy_count": self.policy_count,
            "image_count": self.image_count,
            "verified_image_count": self.verified_image_count,
            "formal_topic_count": self.formal_topic_count,
            "section_count": self.section_count,
            "total_articles": self.total_articles,
            "is_publishable": self.is_publishable,
            "is_complete": self.is_complete,
            "gaps": self.gaps(),
        }


@dataclass
class StepRecord:
    """一次工具调用的执行记录。"""

    step_index: int
    tool_name: str
    arguments: dict[str, Any]
    result_summary: str
    duration_seconds: float
    harness_blocked: bool = False
    block_reason: str = ""
    tokens_used: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "duration_seconds": self.duration_seconds,
            "harness_blocked": self.harness_blocked,
            "block_reason": self.block_reason,
            "tokens_used": self.tokens_used,
            "timestamp": self.timestamp,
        }


class WorkingMemory:
    """
    Agent 在一次运行中的认知状态。

    WorkingMemory 让 Agent 知道：
      - "我已经搜过什么" → 避免重复搜索
      - "我已经读过什么" → 避免重复阅读
      - "我发现了哪些有价值的内容" → 积累知识
      - "还有哪些方向值得探索" → 主动发现新线索
      - "哪些方向我已经放弃了" → 不走回头路
    """

    def __init__(self) -> None:
        # 并发安全锁（asyncio 单线程下主要用于防御性保护）
        self._lock: asyncio.Lock = asyncio.Lock()

        # 已搜索
        self.searched_queries: list[str] = []
        self._searched_queries_normalized: set[str] = set()

        # 原始搜索结果存储（供编排器提取候选 URL）
        self.search_results: list[dict[str, Any]] = []
        self.image_search_results: list[dict[str, Any]] = []

        # URL → 搜索 query 映射（追踪哪篇文章是哪个 query 发现的）
        self.url_search_query: dict[str, str] = {}

        # 已尝试抓取 / 成功可读
        self.attempted_urls: set[str] = set()
        self.read_urls: set[str] = set()

        # 页面链接缓存（url → links list），供 follow_references 使用
        self.page_links_cache: dict[str, list[dict[str, str]]] = {}
        self.page_read_meta: dict[str, dict[str, Any]] = {}

        # 已发现的文章（所有可能有用的，包括尚未评估的）
        self.discovered_articles: list[ArticleSummary] = []

        # 待探索队列（从已读内容发现的新线索）
        self.exploration_queue: list[ExplorationLead] = []

        # 已拒绝的方向（避免重走）
        self.rejected_directions: list[str] = []

        # 关键跨文章洞察
        self.key_findings: list[str] = []

        # 图片候选（article_url → 候选列表）
        self.image_candidates: dict[str, list[ImageCandidate]] = {}

        # 已写板块内容缓存（section → markdown），供 _build_result 收集
        self.sections_content: dict[str, str] = {}
        self.compiled_topics: dict[str, list[dict[str, Any]]] = {}
        self.section_generation_mode: dict[str, str] = {}
        self.section_write_timeouts: list[str] = []

        # 步骤历史（完整 trace）
        self.step_history: list[StepRecord] = []

        # 每步 Agent 的思考过程（自由文本）
        self.thoughts: list[str] = []

        # 覆盖状态（动态维护）
        self.coverage: CoverageState = CoverageState()

        # 诊断与质量观测
        self.candidate_rejection_reasons: dict[str, int] = {}
        self.scrape_layer_stats: dict[str, int] = {}
        self.domain_failures: dict[str, dict[str, Any]] = {}
        self.search_provider_health: dict[str, dict[str, Any]] = {}

        # 搜索空结果追踪（用于 stall detection）
        self.consecutive_empty_searches: int = 0
        self.current_recency_hours: int = 36

        # ── 分层时效窗口 ──
        # 不同板块对时效的容忍度不同：产业快讯需要最新，政策和学术可放宽
        self._section_recency_hours: dict[str, int] = {
            "industry": 36,  # 产业动态/价格/设备：36h 主窗口
            "policy": 72,  # 政策标准/监管：72h
            "academic": 168,  # 学术前沿/高校成果：7天
        }
        # 板块→关键词映射（与 _SECTION_HINT_KEYWORDS 对齐）
        self._recency_section_keywords: dict[str, list[str]] = {
            "policy": [
                "政策",
                "法规",
                "标准",
                "cbam",
                "epr",
                "限塑",
                "监管",
                "compliance",
                "tariff",
                "recycling",
                "循环",
                "禁塑",
                "regulation",
                "standard",
                "directive",
                "ppwr",
                "packaging",
                "food contact",
            ],
            "academic": [
                "研究",
                "突破",
                "大学",
                "实验室",
                "期刊",
                "paper",
                "journal",
                "polymerization",
                "synthesis",
                "novel",
                "discovery",
                "材料科学",
                "高分子材料",
                "university",
                "research",
                "breakthrough",
                "lab",
                "study",
            ],
            "industry": [
                "注塑",
                "挤出",
                "设备",
                "machine",
                "plant",
                "产能",
                "扩产",
                "工厂",
                "量产",
                "automotive",
                "medical",
                "价格",
                "行情",
                "树脂",
                "助剂",
                "添加剂",
                "涨价",
                "供应",
                "injection",
                "extrusion",
                "price",
                "market",
                "resin",
                "plastics",
                "polymer",
                "processing",
                "manufacturing",
                "production",
                "industry",
                "news",
                "latest",
                "update",
                "trend",
                "technology",
                "material",
                "chemical",
                "compound",
                "development",
                "application",
            ],
        }

    # ── 搜索记录 ──────────────────────────────────────────

    def has_searched(self, query: str) -> bool:
        q = query.strip().lower()
        return q in self._searched_queries_normalized

    def record_search(self, query: str) -> None:
        q = query.strip().lower()
        if q not in self._searched_queries_normalized:
            self.searched_queries.append(query)
            self._searched_queries_normalized.add(q)

    def record_search_result_urls(self, query: str, urls: list[str]) -> None:
        """记录搜索 query 与其发现的 URL 之间的映射。"""
        for url in urls:
            if url not in self.url_search_query:
                self.url_search_query[url] = query

    def record_search_results(self, query: str, results: list[dict[str, Any]]) -> None:
        article_urls: list[str] = []
        for row in results:
            normalized = dict(row)
            if normalized.get("url"):
                normalized["url"] = normalize_external_url(str(normalized["url"]))
            if normalized.get("image_url"):
                normalized["image_url"] = normalize_external_url(
                    str(normalized["image_url"])
                )
            result_type = row.get("result_type") or row.get("search_type") or "web"
            if result_type == "images":
                self.image_search_results.append(normalized)
                continue
            self.search_results.append(normalized)
            url = normalized.get("url")
            if url:
                article_urls.append(url)
        self.record_search_result_urls(query, article_urls)

    def record_search_provider_health(
        self, provider: str, snapshot: dict[str, Any]
    ) -> None:
        if provider:
            self.search_provider_health[provider] = dict(snapshot)

    def get_raw_content_for_url(self, url: str) -> str:
        normalized_url = canonicalize_url(url)
        for row in self.search_results:
            if row.get("url") == normalized_url:
                raw = row.get("raw_content") or ""
                if raw:
                    return raw
        return ""

    # ── 阅读记录 ──────────────────────────────────────────

    def has_read(self, url: str) -> bool:
        return canonicalize_url(url) in self.read_urls

    def has_attempted_read(self, url: str) -> bool:
        return canonicalize_url(url) in self.attempted_urls

    def record_read(
        self,
        url: str,
        links: list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.record_page_attempt(url, "readable", links=links, metadata=metadata)

    def record_page_attempt(
        self,
        url: str,
        status: str,
        links: list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_url = canonicalize_url(url)
        self.attempted_urls.add(normalized_url)
        if status == "readable":
            self.read_urls.add(normalized_url)
        if links and status == "readable":
            self.page_links_cache[normalized_url] = links
        merged_meta = dict(self.page_read_meta.get(normalized_url, {}))
        merged_meta["read_state"] = status
        if metadata:
            merged_meta.update(metadata)
        self.page_read_meta[normalized_url] = merged_meta

    def get_page_links(self, url: str) -> list[dict[str, str]]:
        """获取某个已读页面中提取的链接（供 follow_references 使用）。"""
        return self.page_links_cache.get(canonicalize_url(url), [])

    def get_read_metadata(self, url: str) -> dict[str, Any]:
        return dict(self.page_read_meta.get(canonicalize_url(url), {}))

    # ── 文章记录 ──────────────────────────────────────────

    def add_article(self, article: ArticleSummary) -> None:
        # 避免重复 URL
        if any(a.url == article.url for a in self.discovered_articles):
            return
        self.discovered_articles.append(article)
        self._update_coverage(article)

    def publishable_articles(self) -> list[ArticleSummary]:
        return [a for a in self.discovered_articles if a.worth_publishing]

    def sync_article_card(self, card: Any) -> None:
        """将 ArticleAgent 的最终卡片状态回写到已记录的 ArticleSummary。

        evaluate_article 会先把文章写入 WorkingMemory，但图片是在 ArticleAgent
        后续步骤中才确定的。若不回写，Phase 3 读取 memory.publishable_articles()
        时会看不到已验证图片。
        """
        card_url = canonicalize_url(getattr(card, "url", "") or "")
        resolved_url = canonicalize_url(getattr(card, "resolved_url", "") or "")
        image_url = normalize_external_url(getattr(card, "image_url", "") or "") or None
        for article in self.discovered_articles:
            article_url = canonicalize_url(article.url)
            article_resolved = canonicalize_url(article.resolved_url or "")
            if card_url not in {article_url, article_resolved} and resolved_url not in {
                article_url,
                article_resolved,
            }:
                continue

            article.title = getattr(card, "title", article.title) or article.title
            article.source_name = (
                getattr(card, "source_name", article.source_name) or article.source_name
            )
            article.domain = getattr(card, "domain", article.domain) or article.domain
            article.published_at = (
                getattr(card, "published_at", article.published_at)
                or article.published_at
            )
            article.summary = (
                getattr(card, "summary", article.summary) or article.summary
            )
            article.section = (
                getattr(card, "section", article.section) or article.section
            )
            article.key_finding = (
                getattr(card, "key_finding", article.key_finding) or article.key_finding
            )
            article.resolved_url = (
                getattr(card, "resolved_url", article.resolved_url)
                or article.resolved_url
            )
            article.image_url = image_url
            article.has_image = bool(image_url)
            return

    def _update_coverage(self, article: ArticleSummary) -> None:
        if not article.worth_publishing:
            return
        if article.section == "academic":
            self.coverage.academic_count += 1
        elif article.section == "industry":
            self.coverage.industry_count += 1
        elif article.section == "policy":
            self.coverage.policy_count += 1

    def rebuild_coverage(self) -> None:
        verified_images = self.coverage.verified_image_count
        image_count = self.coverage.image_count
        formal_topic_count = self.coverage.formal_topic_count
        self.coverage = CoverageState(
            image_count=image_count,
            verified_image_count=verified_images,
            formal_topic_count=formal_topic_count,
        )
        for article in self.publishable_articles():
            self._update_coverage(article)

    def set_formal_topic_count(self, count: int) -> None:
        self.coverage.formal_topic_count = max(0, int(count))

    # ── 图片记录 ──────────────────────────────────────────

    def add_image_candidate(self, article_url: str, candidate: ImageCandidate) -> None:
        if article_url not in self.image_candidates:
            self.image_candidates[article_url] = []
        self.image_candidates[article_url].append(candidate)
        self.coverage.image_count += 1
        if candidate.verified:
            self.coverage.verified_image_count += 1

    def mark_image_verified(
        self, article_url: str, image_url: str, reason: str
    ) -> None:
        for candidate in self.image_candidates.get(article_url, []):
            if candidate.image_url == image_url and not candidate.verified:
                candidate.verified = True
                candidate.verification_reason = reason
                self.coverage.verified_image_count += 1
                break

    def best_image_for_article(self, article_url: str) -> ImageCandidate | None:
        candidates = self.image_candidates.get(article_url, [])
        verified = [c for c in candidates if c.verified]
        if verified:
            return max(verified, key=lambda c: c.relevance_score)
        if candidates:
            return max(candidates, key=lambda c: c.relevance_score)
        return None

    # ── 线索队列 ──────────────────────────────────────────

    def add_exploration_lead(self, lead: ExplorationLead) -> None:
        if canonicalize_url(lead.url) in self.read_urls:
            return
        if any(l.url == lead.url for l in self.exploration_queue):
            return
        self.exploration_queue.append(lead)
        self.exploration_queue.sort(key=lambda l: l.priority, reverse=True)
        if len(self.exploration_queue) > 20:
            self.exploration_queue = self.exploration_queue[:20]

    def pop_best_lead(self) -> ExplorationLead | None:
        if not self.exploration_queue:
            return None
        self.exploration_queue.sort(key=lambda l: l.priority, reverse=True)
        return self.exploration_queue.pop(0)

    # ── 拒绝方向 ──────────────────────────────────────────

    def reject_direction(self, reason: str) -> None:
        if reason not in self.rejected_directions:
            self.rejected_directions.append(reason)

    # ── 洞察记录 ──────────────────────────────────────────

    def add_finding(self, finding: str) -> None:
        if finding and finding not in self.key_findings:
            self.key_findings.append(finding)

    # ── 步骤记录 ──────────────────────────────────────────

    def record_step(self, step: StepRecord) -> None:
        self.step_history.append(step)

    def record_thought(self, thought: str) -> None:
        self.thoughts.append(thought)

    def record_candidate_rejection(self, reason: str) -> None:
        self.candidate_rejection_reasons[reason] = (
            self.candidate_rejection_reasons.get(reason, 0) + 1
        )

    def record_scrape_layer(self, layer: str) -> None:
        if not layer:
            return
        self.scrape_layer_stats[layer] = self.scrape_layer_stats.get(layer, 0) + 1

    def record_domain_failure(self, domain: str, reason: str) -> None:
        if not domain:
            return
        bucket = self.domain_failures.setdefault(domain, {"count": 0, "reasons": []})
        bucket["count"] += 1
        reasons = bucket.setdefault("reasons", [])
        if reason and reason not in reasons:
            reasons.append(reason[:120])

    def record_empty_search(self) -> None:
        self.consecutive_empty_searches += 1
        if self.consecutive_empty_searches >= 3 and self.current_recency_hours < 48:
            self.current_recency_hours = 48
        elif self.consecutive_empty_searches >= 5 and self.current_recency_hours < 72:
            self.current_recency_hours = 72

    def record_productive_search(self) -> None:
        self.consecutive_empty_searches = 0

    def get_recency_hours_for_query(self, query: str) -> int:
        """Given a search query, return the appropriate recency window based on
        which section it targets.  Policy queries get 72h, academic get 168h,
        everything else falls back to the global current_recency_hours (starts 36h)."""
        ql = query.lower()
        for section, hours in self._section_recency_hours.items():
            keywords = self._recency_section_keywords.get(section, [])
            if any(kw in ql for kw in keywords):
                return hours
        return self.current_recency_hours

    # ── 板块内容缓存 ──────────────────────────────────────

    def cache_section_content(self, section: str, content: str) -> None:
        """缓存 write_section 生成的板块 markdown。"""
        if section and content:
            self.sections_content[section] = content

    def cache_compiled_topics(self, section: str, topics: list[dict[str, Any]]) -> None:
        self.compiled_topics[section] = topics

    def get_compiled_topics(self, section: str) -> list[dict[str, Any]]:
        return list(self.compiled_topics.get(section, []))

    def record_section_generation(
        self, section: str, mode: str, timed_out: bool = False
    ) -> None:
        if section:
            self.section_generation_mode[section] = mode
        if timed_out and section and section not in self.section_write_timeouts:
            self.section_write_timeouts.append(section)

    def get_all_sections_content(self) -> dict[str, str]:
        """返回所有已写的板块内容。"""
        return dict(self.sections_content)

    # ── 上下文摘要（给 LLM 看的） ─────────────────────────

    def to_context_summary(self) -> str:
        """生成给 LLM 的工作记忆摘要，让 Agent 知道自己的当前状态。"""
        parts = []

        # 阶段判断
        search_count = len(self.searched_queries)
        article_count = len(self.publishable_articles())
        written_sections = list(self.sections_content.keys())

        if search_count < 8:
            phase = "广度搜索"
        elif article_count < 6:
            phase = "深度评估"
        else:
            phase = "撰写收尾"
        parts.append(f"📍 当前阶段：{phase}")

        if self.searched_queries:
            parts.append(
                f"已搜索 {search_count} 个查询：{', '.join(self.searched_queries[-5:])}"
            )

        if self.attempted_urls:
            parts.append(f"已尝试抓取 {len(self.attempted_urls)} 个页面")
        if self.read_urls:
            parts.append(f"成功可读 {len(self.read_urls)} 个页面")

        if pub_articles := self.publishable_articles():
            section_info: dict[str, list[str]] = {}
            for a in pub_articles:
                section_info.setdefault(a.section, []).append(a.title)
            section_parts = [f"{s}: {len(t)} 条" for s, t in section_info.items()]
            parts.append(
                f"已确认 {article_count} 篇有价值的文章（{', '.join(section_parts)}）"
            )
        if self.coverage.formal_topic_count > 0:
            parts.append(f"规则层正式主题 {self.coverage.formal_topic_count} 条")

        if written_sections:
            unwritten = {"industry", "academic", "policy"} - set(written_sections)
            parts.append(
                f"已写板块：{', '.join(written_sections)}"
                + (f" | 待写：{', '.join(unwritten)}" if unwritten else "")
            )

        if self.coverage.verified_image_count > 0:
            parts.append(f"已验证 {self.coverage.verified_image_count} 张配图")

        gaps = self.coverage.gaps()
        if gaps:
            parts.append(f"缺口：{'; '.join(gaps)}")

        if self.exploration_queue:
            parts.append(f"待探索线索 {len(self.exploration_queue)} 条")

        if self.key_findings:
            parts.append(f"关键发现：{'; '.join(self.key_findings[-3:])}")

        if self.consecutive_empty_searches >= 2:
            parts.append(
                f"⚠️ 连续 {self.consecutive_empty_searches} 次搜索无可用结果，"
                f"时效窗口已扩大到 {self.current_recency_hours}h"
            )

        # 阶段性建议
        if phase == "广度搜索":
            parts.append("💡 建议：继续搜索不同维度，覆盖产业/技术/政策")
        elif phase == "深度评估":
            parts.append(
                "💡 建议：用 read_page 阅读有价值的搜索结果并评估，补足缺口板块"
            )
        elif phase == "撰写收尾":
            parts.append("💡 建议：调用 write_section 撰写各板块内容，然后调用 finish")

        return "\n".join(parts) if parts else "刚开始，尚无发现。"

    # ── 序列化 ────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """序列化为 dict，用于写入 AgentRun.memory_snapshot。"""
        return {
            "searched_queries": self.searched_queries,
            "search_results_count": len(self.search_results),
            "image_search_results_count": len(self.image_search_results),
            "attempted_urls": list(self.attempted_urls),
            "read_urls": list(self.read_urls),
            "discovered_count": len(self.discovered_articles),
            "publishable_count": len(self.publishable_articles()),
            "exploration_queue_size": len(self.exploration_queue),
            "rejected_directions": self.rejected_directions,
            "key_findings": self.key_findings,
            "coverage": self.coverage.to_dict(),
            "articles": [a.to_dict() for a in self.publishable_articles()],
            "image_candidates": {
                url: [c.to_dict() for c in candidates]
                for url, candidates in self.image_candidates.items()
            },
            "step_count": len(self.step_history),
            "candidate_rejection_reasons": dict(self.candidate_rejection_reasons),
            "scrape_layer_stats": dict(self.scrape_layer_stats),
            "domain_failures": dict(self.domain_failures),
            "search_provider_health": dict(self.search_provider_health),
            "compiled_topics": dict(self.compiled_topics),
            "section_generation_mode": dict(self.section_generation_mode),
            "section_write_timeouts": list(self.section_write_timeouts),
            "consecutive_empty_searches": self.consecutive_empty_searches,
            "current_recency_hours": self.current_recency_hours,
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
