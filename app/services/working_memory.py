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
from datetime import datetime
from typing import Any


@dataclass
class ArticleSummary:
    """Agent 发现的一篇文章的摘要信息。"""
    title: str
    url: str
    domain: str
    source_name: str
    published_at: str | None
    summary: str
    section: str          # academic / industry / policy
    key_finding: str      # Agent 从这篇文章提炼的核心洞察
    has_image: bool = False
    image_url: str | None = None
    worth_publishing: bool = True
    evaluation_reason: str = ""

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
        }


@dataclass
class ImageCandidate:
    """Agent 找到的图片候选。"""
    image_url: str
    source_url: str
    caption: str
    relevance_score: float        # 0-1
    origin_type: str              # article_inline / search_result / og_image
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
    reason: str         # 为什么值得探索
    priority: float     # 0-1，越高越优先


@dataclass
class CoverageState:
    """当前覆盖状态检查。"""
    academic_count: int = 0
    industry_count: int = 0
    policy_count: int = 0
    image_count: int = 0
    verified_image_count: int = 0

    @property
    def section_count(self) -> int:
        return sum([
            1 if self.academic_count > 0 else 0,
            1 if self.industry_count > 0 else 0,
            1 if self.policy_count > 0 else 0,
        ])

    @property
    def total_articles(self) -> int:
        return self.academic_count + self.industry_count + self.policy_count

    @property
    def is_publishable(self) -> bool:
        """至少 6 条 + 2 个板块。"""
        return self.total_articles >= 6 and self.section_count >= 2

    @property
    def is_complete(self) -> bool:
        """至少 8 条 + 3 个板块 + 3 张图。"""
        return (
            self.total_articles >= 8
            and self.section_count >= 3
            and self.verified_image_count >= 3
        )

    def gaps(self) -> list[str]:
        gaps = []
        if self.total_articles < 6:
            gaps.append(f"文章不足（{self.total_articles}/6）")
        if self.section_count < 2:
            missing = []
            if self.industry_count == 0:
                missing.append("产业动态")
            if self.policy_count == 0:
                missing.append("政策标准")
            if self.academic_count == 0:
                missing.append("学术前沿")
            gaps.append(f"板块不足，缺少：{', '.join(missing[:2])}")
        if self.verified_image_count < 2:
            gaps.append(f"图片不足（{self.verified_image_count}/2）")
        return gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "academic_count": self.academic_count,
            "industry_count": self.industry_count,
            "policy_count": self.policy_count,
            "image_count": self.image_count,
            "verified_image_count": self.verified_image_count,
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
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "duration_seconds": self.duration_seconds,
            "harness_blocked": self.harness_blocked,
            "block_reason": self.block_reason,
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

        # 已阅读
        self.read_urls: set[str] = set()

        # 页面链接缓存（url → links list），供 follow_references 使用
        self.page_links_cache: dict[str, list[dict[str, str]]] = {}

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

        # 步骤历史（完整 trace）
        self.step_history: list[StepRecord] = []

        # 每步 Agent 的思考过程（自由文本）
        self.thoughts: list[str] = []

        # 覆盖状态（动态维护）
        self.coverage: CoverageState = CoverageState()

    # ── 搜索记录 ──────────────────────────────────────────

    def has_searched(self, query: str) -> bool:
        q = query.strip().lower()
        return q in self._searched_queries_normalized

    def record_search(self, query: str) -> None:
        q = query.strip().lower()
        if q not in self._searched_queries_normalized:
            self.searched_queries.append(query)
            self._searched_queries_normalized.add(q)

    # ── 阅读记录 ──────────────────────────────────────────

    def has_read(self, url: str) -> bool:
        return url in self.read_urls

    def record_read(self, url: str, links: list[dict[str, str]] | None = None) -> None:
        self.read_urls.add(url)
        if links:
            self.page_links_cache[url] = links

    def get_page_links(self, url: str) -> list[dict[str, str]]:
        """获取某个已读页面中提取的链接（供 follow_references 使用）。"""
        return self.page_links_cache.get(url, [])

    # ── 文章记录 ──────────────────────────────────────────

    def add_article(self, article: ArticleSummary) -> None:
        # 避免重复 URL
        if any(a.url == article.url for a in self.discovered_articles):
            return
        self.discovered_articles.append(article)
        self._update_coverage(article)

    def publishable_articles(self) -> list[ArticleSummary]:
        return [a for a in self.discovered_articles if a.worth_publishing]

    def _update_coverage(self, article: ArticleSummary) -> None:
        if not article.worth_publishing:
            return
        if article.section == "academic":
            self.coverage.academic_count += 1
        elif article.section == "industry":
            self.coverage.industry_count += 1
        elif article.section == "policy":
            self.coverage.policy_count += 1

    # ── 图片记录 ──────────────────────────────────────────

    def add_image_candidate(self, article_url: str, candidate: ImageCandidate) -> None:
        if article_url not in self.image_candidates:
            self.image_candidates[article_url] = []
        self.image_candidates[article_url].append(candidate)
        self.coverage.image_count += 1
        if candidate.verified:
            self.coverage.verified_image_count += 1

    def mark_image_verified(self, article_url: str, image_url: str, reason: str) -> None:
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
        if lead.url in self.read_urls:
            return
        if any(l.url == lead.url for l in self.exploration_queue):
            return
        self.exploration_queue.append(lead)

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

    # ── 板块内容缓存 ──────────────────────────────────────

    def cache_section_content(self, section: str, content: str) -> None:
        """缓存 write_section 生成的板块 markdown。"""
        if section and content:
            self.sections_content[section] = content

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

        if search_count < 6:
            phase = "广度搜索"
        elif article_count < 4:
            phase = "深度评估"
        else:
            phase = "撰写收尾"
        parts.append(f"📍 当前阶段：{phase}")

        if self.searched_queries:
            parts.append(f"已搜索 {search_count} 个查询：{', '.join(self.searched_queries[-5:])}")

        if self.read_urls:
            parts.append(f"已阅读 {len(self.read_urls)} 个页面")

        if pub_articles := self.publishable_articles():
            section_info: dict[str, list[str]] = {}
            for a in pub_articles:
                section_info.setdefault(a.section, []).append(a.title)
            section_parts = [f"{s}: {len(t)} 条" for s, t in section_info.items()]
            parts.append(f"已确认 {article_count} 篇有价值的文章（{', '.join(section_parts)}）")

        if written_sections:
            unwritten = {"industry", "academic", "policy"} - set(written_sections)
            parts.append(f"已写板块：{', '.join(written_sections)}"
                         + (f" | 待写：{', '.join(unwritten)}" if unwritten else ""))

        if self.coverage.verified_image_count > 0:
            parts.append(f"已验证 {self.coverage.verified_image_count} 张配图")

        gaps = self.coverage.gaps()
        if gaps:
            parts.append(f"缺口：{'; '.join(gaps)}")

        if self.exploration_queue:
            parts.append(f"待探索线索 {len(self.exploration_queue)} 条")

        if self.key_findings:
            parts.append(f"关键发现：{'; '.join(self.key_findings[-3:])}")

        # 阶段性建议
        if phase == "广度搜索":
            parts.append("💡 建议：继续搜索不同维度，覆盖产业/技术/政策")
        elif phase == "深度评估":
            parts.append("💡 建议：用 read_page 阅读有价值的搜索结果并评估，补足缺口板块")
        elif phase == "撰写收尾":
            parts.append("💡 建议：调用 write_section 撰写各板块内容，然后调用 finish")

        return "\n".join(parts) if parts else "刚开始，尚无发现。"

    # ── 序列化 ────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """序列化为 dict，用于写入 AgentRun.memory_snapshot。"""
        return {
            "searched_queries": self.searched_queries,
            "search_results_count": len(self.search_results),
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
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
