"""
tools.py — Agent 工具集

包含 9 个可组合的工具，Agent 可以按任意顺序、任意组合使用。

每个工具：
  1. 有清晰的 name 和 description（给 LLM 读的）
  2. 有 parameters JSON schema（LLM 需要填的参数）
  3. 有 execute() 方法（实际执行逻辑）
  4. 返回人类可读 + LLM 可读的结果

工具分类：
  🔍 检索：web_search, read_page, follow_references
  📊 分析：evaluate_article, compare_sources, check_coverage
  🖼️  图片：search_images, verify_image
  ✍️  写作：write_section, finish
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from app.services.source_quality import (
    classify_source,
    detect_page_kind,
    is_valid_price_content,
)
from app.services.working_memory import (
    ArticleSummary,
    ExplorationLead,
    ImageCandidate,
)
from app.utils import (
    extract_domain,
    normalize_external_url,
    now_local,
    parse_datetime,
    summarize_markdown,
)

if TYPE_CHECKING:
    from app.services.llm_client import LLMClient
    from app.services.working_memory import WorkingMemory

logger = logging.getLogger(__name__)
_YEAR_TOKEN_RE = re.compile(r"20(?:24|25|26|27|28)(?:年)?")

_OFF_TOPIC_REJECT_PATTERNS = [
    "market forecast",
    "cagr",
    "stock",
    "earnings",
    "quarterly results",
    "marathon",
    "football",
    "soccer",
    "basketball",
    "tennis",
    "war",
    "missile",
    "military",
    "ophthalmology",
    "biogen",
    "apellis",
    "drug",
    "pharma",
    "财经",
    "股价",
    "财报",
    "马拉松",
    "足球",
    "战争",
    "导弹",
    "医药并购",
]


# ── Base ─────────────────────────────────────────────────


@dataclass
class ToolResult:
    """工具执行结果。"""

    success: bool
    summary: str  # Agent 可读的结果摘要（用于 message history）
    data: dict[str, Any]  # 结构化数据（用于 WorkingMemory 记录）

    def to_message(self) -> str:
        """序列化为 LLM message content。"""
        if self.success:
            return f"✅ {self.summary}"
        return f"❌ {self.summary}"


@dataclass
class ToolCall:
    """Agent 的一次工具调用（来自 LLMClient.ToolCallRequest）。"""

    tool_name: str
    arguments: dict[str, Any]


class Tool:
    """工具基类。子类需实现 execute() 方法。"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        raise NotImplementedError


# ── 不可信域名（统一引用 Harness 的黑名单） ──────────────────
from app.services.harness import DEFAULT_BLOCKED_DOMAINS as _BLOCKED_DOMAINS
from app.services.harness import DEFAULT_DOMAIN_KEYWORDS as _DOMAIN_KEYWORDS

# 评估/提取用的关键词（小写预计算）
_KEYWORDS_LOWER: list[str] = [kw.lower() for kw in _DOMAIN_KEYWORDS] + [
    "发现",
    "突破",
    "进展",
    "研究",
    "技术",
    "创新",
]

# 构建快速查找 set
_BLOCKED_DOMAIN_SET: set[str] = set(_BLOCKED_DOMAINS)
# 台湾地区 TLD（仅用于地区标签，不再作为硬过滤条件）
_TW_TLDS = (".com.tw", ".org.tw", ".edu.tw")
_FOLLOW_REF_REJECT_PAGE_KINDS = {
    "download",
    "search",
    "product",
    "about",
    "homepage",
    "navigation",
    "anti_bot",
    "binary",
}


def _is_blocked_domain(domain: str) -> bool:
    """检查域名或其父域名是否在屏蔽列表中。"""
    if domain in _BLOCKED_DOMAIN_SET:
        return True
    # 支持子域名匹配：geerpower.en.made-in-china.com → made-in-china.com
    parts = domain.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in _BLOCKED_DOMAIN_SET:
            return True
    return False


def _region_tag(domain: str) -> str:
    """根据域名判断地区标签。"""
    if domain in _BLOCKED_DOMAIN_SET or domain.endswith(_TW_TLDS):
        return " [台湾来源]"
    if domain.endswith(".hk") or domain in {"hk01.com"}:
        return " [香港来源]"
    if domain.endswith(".cn") or ".com.cn" in domain:
        return " [大陆来源]"
    return ""


# ── 1. web_search ────────────────────────────────────────


class WebSearchTool(Tool):
    """搜索网页和新闻。Agent 自主决定搜什么。"""

    name = "web_search"
    description = (
        "搜索网页和新闻，发现相关文章链接。"
        "你可以自由构造搜索词，系统会同时搜索 web 和 news 源。"
        "如果某个方向你已经搜过，请换一个角度。"
        "搜索后发现有价值的结果，请用 read_page 深入阅读。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索词，支持中文和英文。建议 5-15 字，不要加引号或特殊语法。",
            },
            "language": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "搜索语言，zh 为中文，en 为英文。",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        brave_client: Any = None,
        zhipu_client: Any = None,
        tavily_api_key: str = "",
    ) -> None:
        self._brave = brave_client
        self._zhipu = zhipu_client
        self._tavily_key = tavily_api_key

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = _YEAR_TOKEN_RE.sub("", query)
        normalized = re.sub(r"\s{2,}", " ", normalized).strip()
        return normalized or query.strip()

    @staticmethod
    def _should_skip_provider(
        memory: "WorkingMemory", provider: str, blocked_states: set[str]
    ) -> bool:
        snapshot = (memory.search_provider_health or {}).get(provider, {})
        state = str(snapshot.get("health_state") or snapshot.get("state") or "")
        return state in blocked_states

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "").strip()
        language: str = kwargs.get("language", "zh")

        normalized_query = self._normalize_query(query)

        if not query:
            return ToolResult(success=False, summary="query 不能为空", data={})

        if memory.has_searched(normalized_query):
            return ToolResult(
                success=False,
                summary=f"'{normalized_query}' 已经搜索过，请换一个不同的搜索词",
                data={"already_searched": True},
            )

        memory.record_search(normalized_query)

        # ── 搜索策略：中文用智谱 Web Search（search_pro），英文/失败时用 Brave ──
        results: list[dict[str, Any]] = []

        if language == "zh" and self._zhipu and self._zhipu.enabled:
            try:
                results = await self._zhipu.search(normalized_query)
                logger.info(
                    "web_search [zh/zhipu] '%s' → %d results",
                    normalized_query,
                    len(results),
                )
            except Exception as exc:
                logger.warning("ZhipuSearch failed for '%s': %s", normalized_query, exc)
            finally:
                memory.record_search_provider_health(
                    "zhipu", self._zhipu.health_snapshot()
                )

        if not results and self._tavily_key:
            tavily_results = await self._search_tavily(normalized_query)
            if tavily_results:
                results = tavily_results
                logger.info(
                    "web_search [tavily] '%s' → %d results",
                    normalized_query,
                    len(results),
                )

        if (
            not results
            and self._brave
            and self._brave.enabled
            and not self._should_skip_provider(
                memory, "brave", {"quota_limited", "circuit_open"}
            )
        ):
            # 英文搜索 或 中文搜索失败时的 Brave 兜底
            from app.config import settings

            search_lang = (
                settings.brave_search_lang
                if language == "zh"
                else settings.brave_fallback_lang
            )
            try:
                results = await self._brave.search_all(normalized_query, search_lang)
                logger.info(
                    "web_search [%s/brave] '%s' → %d results",
                    language,
                    normalized_query,
                    len(results),
                )
            except Exception as exc:
                brave_health = self._brave.health_snapshot()
                if brave_health.get("last_error") == "quota_exceeded":
                    logger.warning(
                        "Brave quota limited for '%s', switching strategy",
                        normalized_query,
                    )
                else:
                    logger.warning(
                        "Brave search failed for '%s': %s", normalized_query, exc
                    )
            finally:
                memory.record_search_provider_health(
                    "brave", self._brave.health_snapshot()
                )
        elif not results and self._brave and self._brave.enabled:
            logger.info(
                "web_search skipping Brave for '%s' due to run-level health state",
                normalized_query,
            )

        if not results:
            memory.record_empty_search()
            return ToolResult(
                success=True,
                summary=f"'{normalized_query}' 没有找到结果，请尝试不同的搜索词",
                data={"results": []},
            )

        # ── 硬过滤台湾等不可信域名 ──
        filtered = []
        blocked_count = 0
        for r in results:
            domain = r.get("domain") or extract_domain(r.get("url", ""))
            if _is_blocked_domain(domain):
                blocked_count += 1
                continue
            filtered.append(r)
        if blocked_count:
            logger.info(
                "web_search filtered out %d results from blocked domains", blocked_count
            )
        results = filtered

        if not results:
            memory.record_empty_search()
            return ToolResult(
                success=True,
                summary=f"'{normalized_query}' 搜索到了结果但都被过滤了（不可信来源），请换一个搜索词",
                data={"results": []},
            )

        # ── 时效性过滤：按板块使用分层的时效窗口 ──
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=memory.get_recency_hours_for_query(normalized_query)
        )
        fresh_results = []
        stale_count = 0
        for r in results:
            pub = r.get("published_at")
            if pub is not None:
                # 确保 pub 有 timezone 信息
                if hasattr(pub, "tzinfo") and pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    stale_count += 1
                    continue
            # pub 为 None 的保留（无法判断时效）
            fresh_results.append(r)
        if stale_count:
            window_hours = memory.get_recency_hours_for_query(normalized_query)
            logger.info(
                "web_search filtered out %d stale results (>%dh old for this section)",
                stale_count,
                window_hours,
            )
        results = fresh_results

        if not results:
            memory.record_empty_search()
            return ToolResult(
                success=True,
                summary=f"'{normalized_query}' 的搜索结果都超出日报主窗口（约 {memory.current_recency_hours} 小时），请换一个更具时效性的搜索词。",
                data={"results": []},
            )

        article_results = [
            r
            for r in results
            if (r.get("result_type") or r.get("search_type")) != "images"
        ]
        image_results = [
            r
            for r in results
            if (r.get("result_type") or r.get("search_type")) == "images"
        ]

        # ── 格式化结果给 LLM 看 ──
        display_limit = 8
        formatted = []
        for r in article_results[:display_limit]:
            url = r.get("url", "")
            domain = r.get("domain") or extract_domain(url)
            region = _region_tag(domain)
            published = r.get("published_at")
            pub_str = published.strftime("%Y-%m-%d") if published else "未知时间"
            snippet = (r.get("snippet") or "")[:200]
            formatted.append(
                f"- [{r.get('title', 'Untitled')}]({url})\n"
                f"  来源: {domain}{region} | {pub_str} | 类型: {(r.get('result_type') or r.get('search_type') or 'web')}\n"
                f"  摘要: {snippet}"
            )

        summary = f"搜索 '{normalized_query}' 找到 {len(article_results)} 条文章结果"
        if image_results:
            summary += f"、{len(image_results)} 条图片结果"
        summary += "：\n\n" + "\n\n".join(formatted)

        # 按结果类型分池存储，避免图片污染文章候选池
        memory.record_search_results(normalized_query, results[:12])
        memory.record_productive_search()

        return ToolResult(
            success=True,
            summary=summary,
            data={
                "query": normalized_query,
                "results": article_results[:10],
                "image_results": image_results[:5],
                "total": len(article_results),
            },
        )

    async def _search_tavily(self, query: str) -> list[dict[str, Any]]:
        if not self._tavily_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    headers={
                        "Authorization": f"Bearer {self._tavily_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "topic": "general",
                        "time_range": "day",
                        "max_results": 10,
                        "search_depth": "basic",
                        "include_raw_content": True,
                        "country": "china",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Tavily search failed for '%s': HTTP %d",
                        query,
                        resp.status_code,
                    )
                    return []
                data = resp.json()
                raw_results = data.get("results", [])
                parsed = []
                for r in raw_results:
                    url = r.get("url", "")
                    domain = extract_domain(url)
                    raw_content = r.get("raw_content") or ""
                    parsed.append(
                        {
                            "title": r.get("title", ""),
                            "url": url,
                            "snippet": r.get("content", "")[:500],
                            "domain": domain,
                            "published_at": None,
                            "result_type": "web",
                            "search_type": "web",
                            "source_type": "web",
                            "source_name": "tavily",
                            "score": r.get("score", 0),
                            "raw_content": raw_content,
                        }
                    )
                return parsed
        except Exception as exc:
            logger.warning("Tavily search failed for '%s': %s", query, exc)
            return []


# ── 2. read_page ─────────────────────────────────────────


class ReadPageTool(Tool):
    """深度阅读一个网页，获取完整内容。"""

    name = "read_page"
    description = (
        "深度阅读一个网页，获取完整正文、发布时间和页面图片。"
        "用于搜索结果中找到有价值的链接后深入阅读。"
        "阅读后请立即用 evaluate_article 评估文章价值。"
        "避免重复阅读同一个 URL。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要阅读的网页 URL。",
            },
        },
        "required": ["url"],
    }

    def __init__(
        self, scraper_client: Any = None, timeout_seconds: int | None = None
    ) -> None:
        self._scraper = scraper_client
        self._timeout_seconds = timeout_seconds

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        url: str = normalize_external_url(kwargs.get("url", "").strip())
        if not url:
            return ToolResult(success=False, summary="url 不能为空", data={})

        if memory.has_read(url):
            return ToolResult(
                success=False,
                summary=f"已经阅读过 {url}，请选择其他链接",
                data={"already_read": True},
            )

        domain = extract_domain(url)

        raw_content = memory.get_raw_content_for_url(url)
        if raw_content and len(raw_content) > 200:
            scrape_layer = "tavily_raw"
            title = raw_content.split("\n", 1)[0].strip()[:120] or "Tavily Raw Content"
            markdown = raw_content
            image_url = None
            published_dt = None
            pub_str = "未知"
            links = self._extract_links(markdown, url)
            memory.record_page_attempt(
                url,
                "readable",
                links=links,
                metadata={
                    "scrape_layer": scrape_layer,
                    "content_available": True,
                    "from_tavily_raw": True,
                },
            )
            memory.record_scrape_layer(scrape_layer)
            content_for_summary = self._extract_content(markdown, max_chars=8000)
            content_summary = summarize_markdown(content_for_summary) or markdown[:500]
            summary = f"📄 {title}\n来源: {domain} (tavily raw) | 发布: {pub_str}\n内容: {content_summary}\n"
            if links:
                summary += f"页内链接 {len(links)} 条（可用 follow_references 追踪）"
            return ToolResult(
                success=True,
                summary=summary,
                data={
                    "url": url,
                    "title": title,
                    "domain": domain,
                    "markdown": markdown[:8000],
                    "content_summary": content_summary,
                    "image_url": None,
                    "published_at": None,
                    "resolved_url": url,
                    "scrape_layer": scrape_layer,
                    "scrape_status": "success",
                    "page_kind": "article",
                    "links": links[:10],
                },
            )

        if self._scraper is None or not self._scraper.enabled:
            memory.record_page_attempt(
                url, "attempted_failed", metadata={"content_available": False}
            )
            return ToolResult(
                success=False,
                summary="页面抓取服务不可用",
                data={},
            )

        try:
            from app.config import settings

            timeout_seconds = self._timeout_seconds or settings.scrape_timeout_seconds
            result = await self._scraper.scrape(url, timeout_seconds=timeout_seconds)
        except Exception as exc:
            memory.record_page_attempt(
                url, "attempted_failed", metadata={"content_available": False}
            )
            logger.warning("read_page failed for %s: %s", url, exc)
            return ToolResult(
                success=False, summary=f"抓取失败: {url} — {exc}", data={"url": url}
            )

        scrape_status = result.get("status") or ""
        markdown = result.get("markdown") or ""
        title = result.get("title") or ""
        image_url = normalize_external_url(result.get("image_url") or "") or None
        published_at = result.get("published_at")
        resolved_url = normalize_external_url(result.get("resolved_url") or url)
        scrape_layer = result.get("scrape_layer") or scrape_status or "unknown"
        page_kind = detect_page_kind(resolved_url, title=title, content=markdown)

        if scrape_status == "error" or not title.strip() or not markdown.strip():
            memory.record_page_attempt(
                url,
                "attempted_failed",
                metadata={
                    "resolved_url": resolved_url,
                    "scrape_status": scrape_status or "attempted_failed",
                    "scrape_layer": scrape_layer,
                    "page_kind": page_kind,
                    "content_available": False,
                },
            )
            memory.record_scrape_layer(scrape_layer)
            return ToolResult(
                success=False,
                summary=f"页面内容不可用: {resolved_url}",
                data={
                    "url": url,
                    "resolved_url": resolved_url,
                    "scrape_layer": scrape_layer,
                },
            )

        if page_kind in {"download", "anti_bot", "binary"}:
            memory.record_page_attempt(
                url,
                "rejected_by_page_kind",
                metadata={
                    "resolved_url": resolved_url,
                    "scrape_status": scrape_status or "success",
                    "scrape_layer": scrape_layer,
                    "page_kind": page_kind,
                    "content_available": False,
                },
            )
            memory.record_scrape_layer(scrape_layer)
            return ToolResult(
                success=False,
                summary=f"页面类型不适合直接作为正文: {page_kind}",
                data={
                    "url": url,
                    "resolved_url": resolved_url,
                    "scrape_layer": scrape_layer,
                    "page_kind": page_kind,
                },
            )

        quality = classify_source(
            url=resolved_url, title=title, content=markdown[:1500]
        )
        if quality.get("publish_block_reason"):
            memory.record_page_attempt(
                url,
                "rejected_by_quality",
                metadata={
                    "resolved_url": resolved_url,
                    "scrape_status": scrape_status or "success",
                    "scrape_layer": scrape_layer,
                    "page_kind": quality["page_kind"],
                    "content_available": False,
                    "quality_rejection_reason": quality["publish_block_reason"],
                },
            )
            memory.record_scrape_layer(scrape_layer)
            return ToolResult(
                success=False,
                summary=f"页面质量不符合正文标准: {quality['publish_block_reason']}",
                data={
                    "url": url,
                    "resolved_url": resolved_url,
                    "scrape_layer": scrape_layer,
                    "page_kind": quality["page_kind"],
                },
            )

        published_dt = (
            published_at
            if isinstance(published_at, datetime)
            else parse_datetime(str(published_at))
            if published_at
            else None
        )
        if published_dt is not None:
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=timezone.utc)
            ref_now = now_local().astimezone(published_dt.tzinfo)
            age_days = max(
                (ref_now.replace(tzinfo=None) - published_dt.replace(tzinfo=None)).days,
                0,
            )
            if age_days > 7:
                memory.record_page_attempt(
                    url,
                    "rejected_by_recency",
                    metadata={
                        "resolved_url": resolved_url,
                        "scrape_status": scrape_status or "success",
                        "scrape_layer": scrape_layer,
                        "page_kind": page_kind,
                        "content_available": False,
                        "published_at": published_dt.isoformat(),
                    },
                )
                memory.record_scrape_layer(scrape_layer)
                return ToolResult(
                    success=False,
                    summary=f"页面发布时间过旧（>{7}天）: {published_dt.date().isoformat()}",
                    data={
                        "url": url,
                        "resolved_url": resolved_url,
                        "scrape_layer": scrape_layer,
                        "page_kind": page_kind,
                    },
                )

        published_at = published_dt
        pub_str = published_at.strftime("%Y-%m-%d %H:%M") if published_at else "未知"

        # 提取页内链接（用于 follow_references）
        links = self._extract_links(markdown, url)
        memory.record_page_attempt(
            url,
            "readable",
            links=links,
            metadata={
                "resolved_url": resolved_url,
                "scrape_status": scrape_status or "success",
                "scrape_layer": scrape_layer,
                "page_kind": page_kind,
                "content_available": True,
            },
        )
        memory.record_scrape_layer(scrape_layer)

        # 智能内容提取：分段取引言 + 结论 + 中间关键词密集段
        content_for_summary = self._extract_content(markdown, max_chars=8000)
        content_summary = summarize_markdown(content_for_summary) or markdown[:500]

        summary = (
            f"📄 {title}\n来源: {domain} | 发布: {pub_str}\n内容: {content_summary}\n"
        )
        if image_url:
            summary += f"主图: {image_url}\n"
        if links:
            summary += f"页内链接 {len(links)} 条（可用 follow_references 追踪）"

        return ToolResult(
            success=True,
            summary=summary,
            data={
                "url": url,
                "title": title,
                "domain": domain,
                "markdown": markdown[:8000],
                "content_summary": content_summary,
                "image_url": image_url,
                "published_at": published_at.isoformat() if published_at else None,
                "resolved_url": resolved_url,
                "scrape_layer": scrape_layer,
                "scrape_status": scrape_status or "success",
                "page_kind": page_kind,
                "links": links[:10],
            },
        )

    @staticmethod
    def _extract_links(markdown: str, base_url: str) -> list[dict[str, str]]:
        """从 markdown 中提取链接。"""
        pattern = r"\[([^\]]+)\]\((https?://[^\)]+)\)"
        links = []
        for match in re.finditer(pattern, markdown):
            text, href = match.group(1), match.group(2)
            # 过滤掉图片、锚点
            if any(
                ext in href.lower() for ext in [".jpg", ".png", ".gif", ".svg", ".pdf"]
            ):
                continue
            domain = extract_domain(href)
            links.append({"text": text, "url": href, "domain": domain})
        return links[:15]

    @staticmethod
    def _extract_content(markdown: str, max_chars: int = 8000) -> str:
        """
        智能分段提取：引言 + 结论 + 中间关键词密集段落。
        替代简单硬截断，保留长文章的关键后半部分内容。
        """
        if len(markdown) <= max_chars:
            return markdown

        head_size = 2500
        tail_size = 1500
        mid_budget = max_chars - head_size - tail_size

        head = markdown[:head_size]
        tail = markdown[-tail_size:] if len(markdown) > tail_size else ""

        # 中间部分分段（按段落分割），选关键词密度最高的
        mid_text = markdown[head_size : len(markdown) - tail_size]
        paragraphs = [p for p in mid_text.split("\n\n") if len(p.strip()) > 50]

        if mid_budget <= 0 or not paragraphs:
            return head + "\n\n...\n\n" + tail

        # 按关键词密度评分（使用模块级预计算的小写关键词列表）
        def keyword_density(text: str) -> float:
            text_lower = text.lower()
            return (
                sum(1 for kw in _KEYWORDS_LOWER if kw in text_lower)
                / max(len(text), 1)
                * 1000
            )

        scored = [(keyword_density(p), p) for p in paragraphs]
        scored.sort(key=lambda x: x[0], reverse=True)

        mid_selected: list[str] = []
        mid_len = 0
        for _, p in scored:
            if mid_len + len(p) > mid_budget:
                break
            mid_selected.append(p)
            mid_len += len(p)

        result = head
        if mid_selected:
            result += "\n\n...[中间重点段落]...\n\n" + "\n\n".join(mid_selected)
        result += "\n\n...[文末]...\n\n" + tail
        return result


# ── 3. follow_references ─────────────────────────────────


class FollowReferencesTool(Tool):
    """从一个网页中发现引用、参考文献和相关链接。"""

    name = "follow_references"
    description = (
        "从一个已阅读的页面中提取引用、参考文献和相关链接，"
        "加入探索队列。用于在一篇文章中发现值得深入的新方向。"
        "不会立即读取，而是将线索加入工作记忆。"
        "只在需要追踪引用来源时使用，普通文章不需要调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "已阅读过的页面 URL（用于从其内容中提取链接）。",
            },
            "focus": {
                "type": "string",
                "description": "你最感兴趣的主题方向，用于过滤相关链接（可选）。",
            },
        },
        "required": ["url"],
    }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        url: str = kwargs.get("url", "").strip()
        focus: str = kwargs.get("focus", "")

        if not url:
            return ToolResult(success=False, summary="url 不能为空", data={})

        if not memory.has_read(url):
            return ToolResult(
                success=False,
                summary=f"'{url}' 尚未阅读，请先用 read_page 阅读该页面再追踪引用",
                data={"not_read": True},
            )

        # 从 WorkingMemory 的页面链接缓存中拉取链接
        page_links = memory.get_page_links(url)
        if not page_links:
            return ToolResult(
                success=True,
                summary=f"'{url}' 中未发现可追踪的引用链接",
                data={"url": url, "leads_added": 0},
            )

        leads_added = 0
        focus_lower = focus.lower()
        for link in page_links[:12]:  # 最多取 12 条
            link_url = link.get("url", "")
            link_text = link.get("text", link_url[:60])
            link_domain = link.get("domain", extract_domain(link_url))

            # 基础过滤：已读/已在队列
            if not link_url or memory.has_read(link_url):
                continue
            if _is_blocked_domain(link_domain):
                continue
            quality = classify_source(url=link_url, title=link_text, content="")
            if quality["page_kind"] in _FOLLOW_REF_REJECT_PAGE_KINDS:
                continue
            if quality["source_tier"] == "D":
                continue
            # 焦点过滤：如果有焦点词，优先匹配标题包含关键词的链接
            relevance = 0.2
            if focus_lower and any(
                kw in link_text.lower() for kw in focus_lower.split()
            ):
                relevance = 0.8

            lead = ExplorationLead(
                url=link_url,
                title=link_text,
                reason=f"从 {extract_domain(url)} 页面发现的引用",
                priority=relevance,
            )
            memory.add_exploration_lead(lead)
            leads_added += 1

        formatted = [
            f"- [{lk.get('text', '...')}]({lk.get('url', '')})" for lk in page_links[:5]
        ]
        summary = (
            f"从 '{url}' 中提取到 {len(page_links)} 个链接，"
            f"加入了 {leads_added} 条探索线索：\n" + "\n".join(formatted)
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={
                "url": url,
                "leads_added": leads_added,
                "total_links": len(page_links),
            },
        )


# ── 4. evaluate_article ──────────────────────────────────


class EvaluateArticleTool(Tool):
    """评估一篇文章是否值得纳入日报。"""

    name = "evaluate_article"
    description = (
        "评估一篇文章的价值：是否值得纳入日报、属于哪个板块、"
        "核心发现是什么。只评估已用 read_page 阅读过的文章，"
        "不要凭搜索摘要评估。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "文章标题"},
            "content": {"type": "string", "description": "文章内容摘要（前 1000 字）"},
            "url": {"type": "string", "description": "文章 URL"},
            "domain": {"type": "string", "description": "来源域名"},
            "published_at": {"type": "string", "description": "发布时间（可选）"},
            "resolved_url": {
                "type": "string",
                "description": "抓取后最终落地 URL（可选）",
            },
            "page_kind": {"type": "string", "description": "页面类型（可选）"},
        },
        "required": ["title", "content", "url"],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        title: str = kwargs.get("title", "")
        content: str = kwargs.get("content", "")
        url: str = normalize_external_url(kwargs.get("url", ""))
        domain: str = kwargs.get("domain", extract_domain(url))
        published_at: str = kwargs.get("published_at", "")
        resolved_url: str = normalize_external_url(kwargs.get("resolved_url", ""))
        page_kind: str = kwargs.get("page_kind", "")

        if not title or not content:
            return ToolResult(
                success=False, summary="需要提供 title 和 content", data={}
            )

        quality = classify_source(url=resolved_url or url, title=title, content=content)
        if page_kind:
            quality["page_kind"] = page_kind
        recency_status = "unknown"
        published_at_source = "missing"
        published_dt = parse_datetime(published_at) if published_at else None
        if published_dt is not None:
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=timezone.utc)
            ref_now = now_local().astimezone(published_dt.tzinfo)
            age_days = max(
                (ref_now.replace(tzinfo=None) - published_dt.replace(tzinfo=None)).days,
                0,
            )
            recency_status = "recent_verified"
            published_at_source = "page_or_metadata"
            if age_days > 7:
                return ToolResult(
                    success=True,
                    summary=f"❌ 价值不足：{title}\n板块: rejected | 发布时间过旧",
                    data={
                        "worthy": False,
                        "section": "rejected",
                        "key_finding": "",
                        "reason": f"发布时间过旧（{published_dt.date().isoformat()}），不纳入今日日报",
                        "image_worthiness": False,
                        "added_to_memory": False,
                        "source_tier": quality["source_tier"],
                        "source_reliability_label": quality["source_reliability_label"],
                        "source_kind": quality["source_kind"],
                        "page_kind": quality["page_kind"],
                        "recency_status": "stale_verified",
                        "published_at_source": published_at_source,
                    },
                )

        hard_reject_reason = self._hard_reject_reason(
            title,
            content,
            domain,
            page_kind=quality["page_kind"],
            quality=quality,
        )
        if hard_reject_reason:
            return ToolResult(
                success=True,
                summary=f"❌ 价值不足：{title}\n板块: rejected | {hard_reject_reason}",
                data={
                    "worthy": False,
                    "section": "rejected",
                    "key_finding": "",
                    "reason": hard_reject_reason,
                    "image_worthiness": False,
                    "added_to_memory": False,
                    "source_tier": quality["source_tier"],
                    "source_reliability_label": quality["source_reliability_label"],
                    "source_kind": quality["source_kind"],
                    "page_kind": quality["page_kind"],
                },
            )

        system_prompt = (
            "你是高分子材料加工领域的日报研究员。\n"
            "评估这篇文章是否值得纳入今日日报，并给出理由。\n"
            "优先选择中国大陆权威媒体或英文学术/产业新闻，对台湾或非相关繁体媒体降低评分权重！\n"
            "对以下内容必须直接拒绝：泛财经市场预测、宏观战争新闻、纯医药并购、体育社会新闻、"
            "与高分子材料加工/设备/原料/政策无直接关系的内容。\n"
            "输出 JSON，包含：\n"
            "  - worthy: true/false\n"
            "  - section: academic/industry/policy\n"
            "  - key_finding: 一句话的核心发现（30字以内，必须是中文）\n"
            "  - reason: 评估理由（50字以内，写明为什么值得或不值得，必须是中文）\n"
            "  - image_worthiness: true/false（这个主题值不值得配图）\n"
            "  - zh_title: 文章的中文翻译标题（如果原文是中文则原样保留）\n"
            "  - zh_summary: 提炼后的中文内容摘要（约80字，如果原文是中文则用中文总结）\n"
        )
        user_content = (
            f"标题：{title}\n"
            f"来源：{domain}\n"
            f"来源等级：{quality['source_tier']} / {quality['source_kind']}\n"
            f"页面类型：{quality['page_kind']}\n"
            f"发布时间：{published_at or '未知（允许继续评估，但需降低时效置信）'}\n"
            f"内容摘要：\n{content[:1200]}"
        )

        if self._llm and self._llm.enabled:
            result = await self._llm.simple_json_completion(system_prompt, user_content)
        else:
            result = self._heuristic_evaluate(title, content, domain)

        worthy = bool(result.get("worthy", False))
        section = result.get("section", "industry")
        key_finding = result.get("key_finding", title[:50])
        reason = result.get("reason", "")
        image_worthiness = bool(result.get("image_worthiness", True))

        zh_title = result.get("zh_title") or title
        zh_summary = result.get("zh_summary") or content[:500]

        if worthy:
            # 查找发现此 URL 的搜索 query
            search_query = memory.url_search_query.get(url, "")

            article = ArticleSummary(
                title=zh_title,
                url=url,
                domain=domain,
                source_name=domain,
                published_at=published_at,
                summary=zh_summary,
                section=section
                if section in {"academic", "industry", "policy"}
                else "industry",
                key_finding=key_finding,
                worth_publishing=True,
                evaluation_reason=reason,
                search_query=search_query,
                resolved_url=resolved_url or None,
                source_tier=quality["source_tier"],
                source_reliability_label=quality["source_reliability_label"],
                source_kind=quality["source_kind"],
                page_kind=quality["page_kind"],
                evidence_strength=quality["evidence_strength"],
                supports_numeric_claims=quality["supports_numeric_claims"],
                allowed_for_trend_summary=quality["allowed_for_trend_summary"],
                is_primary_source=quality["is_primary_source"],
                requires_observation_only=quality["requires_observation_only"],
                recency_status=recency_status,
                published_at_source=published_at_source,
            )
            memory.add_article(article)

            if image_worthiness:
                memory.add_exploration_lead(
                    ExplorationLead(
                        url=url,
                        title=f"[图片] {title}",
                        reason=f"这篇文章值得配图：{key_finding}",
                        priority=0.6,
                    )
                )

        status = "✅ 有价值" if worthy else "❌ 价值不足"
        summary = f"{status}：{title}\n板块: {section} | {reason}"
        if worthy:
            summary += f"\n核心发现: {key_finding}"

        return ToolResult(
            success=True,
            summary=summary,
            data={
                "worthy": worthy,
                "section": section,
                "key_finding": key_finding,
                "reason": reason,
                "image_worthiness": image_worthiness,
                "added_to_memory": worthy,
                "source_tier": quality["source_tier"],
                "source_reliability_label": quality["source_reliability_label"],
                "source_kind": quality["source_kind"],
                "page_kind": quality["page_kind"],
                "evidence_strength": quality["evidence_strength"],
                "supports_numeric_claims": quality["supports_numeric_claims"],
                "allowed_for_trend_summary": quality["allowed_for_trend_summary"],
                "recency_status": recency_status,
                "published_at_source": published_at_source,
            },
        )

    @staticmethod
    def _heuristic_evaluate(title: str, content: str, domain: str) -> dict[str, Any]:
        """LLM 不可用时的启发式评估。"""
        text = f"{title} {content}".lower()
        topic_hits = sum(
            1
            for kw in [
                "高分子",
                "塑料",
                "橡胶",
                "polymer",
                "plastic",
                "注塑",
                "挤出",
                "composite",
                "recycling",
                "material",
            ]
            if kw.lower() in text
        )

        is_academic = any(
            kw in text
            for kw in ["研究", "论文", "paper", "journal", "机理", "mechanism"]
        )
        is_policy = any(
            kw in text for kw in ["政策", "标准", "法规", "regulation", "policy"]
        )
        section = "academic" if is_academic else ("policy" if is_policy else "industry")

        worthy = topic_hits >= 2
        return {
            "worthy": worthy,
            "section": section,
            "key_finding": title[:50],
            "reason": f"关键词命中 {topic_hits} 个" if worthy else "主题相关度不足",
            "image_worthiness": True,
        }

    @staticmethod
    def _hard_reject_reason(
        title: str,
        content: str,
        domain: str,
        *,
        page_kind: str,
        quality: dict[str, Any],
    ) -> str | None:
        text = f"{title} {content} {domain}".lower()
        if quality.get("publish_block_reason"):
            return str(quality["publish_block_reason"])
        if any(pattern in text for pattern in _OFF_TOPIC_REJECT_PATTERNS):
            return "内容与高分子材料加工日报主题弱相关"
        if "globenewswire" in text or "prnewswire" in text:
            return "PR/新闻稿内容，不纳入日报"
        if "merger" in text and not any(
            term in text
            for term in ["polymer", "plastic", "plastics", "树脂", "塑料", "高分子"]
        ):
            return "并购新闻缺少高分子材料加工相关性"
        if page_kind == "price_snapshot" and not is_valid_price_content(title, content):
            return "价格快照页缺少日期、变化和供需解释，不纳入日报"
        return None


# ── 5. compare_sources ───────────────────────────────────


class CompareSourcesTool(Tool):
    """对比多个信息源，去重和找互补。"""

    name = "compare_sources"
    description = (
        "对比当前工作记忆中的所有文章，去除重复事件，"
        "识别互补的报道角度，推荐最终保留哪些。"
        "在收集了足够文章后、写报告前调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": "对比时特别关注的维度（如：去重、多样性、图片覆盖等），可选。",
            },
        },
        "required": [],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        articles = memory.publishable_articles()
        if not articles:
            return ToolResult(
                success=False, summary="当前没有已收集的文章可对比", data={}
            )

        if len(articles) == 1:
            return ToolResult(
                success=True,
                summary=f"只有 1 篇文章，无需对比。文章: {articles[0].title}",
                data={"article_count": 1},
            )

        focus: str = kwargs.get("focus", "均衡报道和去重")

        article_list = "\n".join(
            [
                f"{i + 1}. [{a.section}] {a.title}\n   来源: {a.domain} | 核心: {a.key_finding}"
                for i, a in enumerate(articles)
            ]
        )

        system_prompt = (
            "你是高分子材料加工日报的资深分析师。\n"
            f"请深度分析以下 {len(articles)} 篇文章，完成：\n"
            "1. 去重：找出报道同一事件的文章\n"
            "2. 趋势洞察：跨文章发现行业大趋势，判断信号强度\n"
            "3. 关联分析：哪些文章之间存在因果关系或连锁反应\n\n"
            "输出 JSON，包含：\n"
            "  - duplicates: [[index1, index2], ...] 重复文章的索引对\n"
            "  - keep_indices: [1,2,3,...] 推荐保留的文章索引（1-based）\n"
            "  - analysis: 对比分析（100字以内）\n"
            '  - trends: [{"theme": "趋势主题", "strength": "强/中/弱", '
            '"articles": [1,2], "insight": "跨文章关联洞察（50字以内）"}, ...]\n'
        )

        if self._llm and self._llm.enabled:
            result = await self._llm.simple_json_completion(
                system_prompt, f"关注: {focus}\n\n{article_list}"
            )
        else:
            result = {
                "keep_indices": list(range(1, min(len(articles) + 1, 6))),
                "duplicates": [],
                "analysis": "启发式保留前5篇",
            }

        keep_indices = result.get("keep_indices", [])
        duplicates = result.get("duplicates", [])
        analysis = result.get("analysis", "")
        trends = result.get("trends", []) if isinstance(result, dict) else []
        for trend in trends[:3]:
            insight = trend.get("insight") if isinstance(trend, dict) else None
            if insight:
                memory.add_finding(str(insight))

        summary = (
            f"对比 {len(articles)} 篇文章：\n"
            f"发现 {len(duplicates)} 组重复\n"
            f"辅助建议保留 {len(keep_indices)} 篇\n"
            f"分析：{analysis}"
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={
                "keep_count": len(keep_indices),
                "duplicates": len(duplicates),
                "analysis": analysis,
                "trends": result.get("trends", []),
            },
        )


# ── 6. search_images ─────────────────────────────────────


class SearchImagesTool(Tool):
    """为一个主题主动搜索最佳配图。"""

    name = "search_images"
    description = (
        "为一篇文章或一个主题主动搜索相关配图。"
        "返回图片 URL、来源和相关度。"
        "调用时请提供具体的主题描述，越具体越好。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "图片主题，如'注塑机生产线'、'高分子材料回收'等（越具体越好）",
            },
            "article_url": {
                "type": "string",
                "description": "要为哪篇文章找图（填文章 URL，用于关联）",
            },
        },
        "required": ["topic"],
    }

    def __init__(self, brave_client: Any = None, scraper_client: Any = None) -> None:
        self._brave = brave_client
        self._scraper = scraper_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        topic: str = kwargs.get("topic", "").strip()
        article_url: str = normalize_external_url(kwargs.get("article_url", ""))

        if not topic:
            return ToolResult(success=False, summary="topic 不能为空", data={})

        results: list[dict[str, Any]] = []

        # 优先 Brave 图片搜索
        brave_health = (memory.search_provider_health or {}).get("brave", {})
        brave_state = str(
            brave_health.get("health_state") or brave_health.get("state") or ""
        )
        if (
            self._brave
            and self._brave.enabled
            and brave_state not in {"quota_limited", "circuit_open"}
        ):
            from app.config import settings

            try:
                results = await self._brave.search(
                    topic,
                    search_type="images",
                    count=6,
                    search_lang=settings.brave_search_lang,
                )
            except Exception as exc:
                logger.warning("search_images [brave] failed: %s", exc)
            finally:
                if hasattr(self._brave, "health_snapshot"):
                    memory.record_search_provider_health(
                        "brave", self._brave.health_snapshot()
                    )
        elif self._brave and self._brave.enabled:
            logger.info(
                "search_images skipping Brave for '%s' due to run-level health state",
                topic,
            )

        if not results:
            # 如果有 article_url，尝试从文章页面本身提取图片
            if article_url and self._scraper and self._scraper.enabled:
                try:
                    page_data = await self._scraper.scrape(article_url)
                    og_image = page_data.get("image_url")
                    if og_image:
                        results.append(
                            {
                                "url": article_url,
                                "title": page_data.get("title", topic),
                                "image_url": normalize_external_url(og_image),
                            }
                        )
                        logger.info(
                            "search_images: extracted OG image from article page"
                        )
                except Exception as exc:
                    logger.warning(
                        "search_images: failed to scrape article for image: %s", exc
                    )

        if not results:
            memory.reject_direction(f"图片搜索无结果: {topic}")
            return ToolResult(
                success=True,
                summary=f"主题 '{topic}' 未找到合适图片",
                data={"results": []},
            )

        candidates_added = 0
        formatted = []
        for r in results[:5]:
            img_url = normalize_external_url(r.get("image_url") or r.get("url", ""))
            if not img_url:
                continue
            candidate = ImageCandidate(
                image_url=img_url,
                source_url=normalize_external_url(r.get("url", img_url)),
                caption=r.get("title", topic),
                relevance_score=0.6,
                origin_type="search_result",
            )
            memory.add_image_candidate(article_url or "general", candidate)
            candidates_added += 1
            formatted.append(f"- {r.get('title', 'Untitled')}: {img_url}")

        summary = f"为 '{topic}' 找到 {candidates_added} 张候选图片：\n" + "\n".join(
            formatted
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={"topic": topic, "candidates_added": candidates_added},
        )


# ── 7. verify_image ──────────────────────────────────────


class VerifyImageTool(Tool):
    """验证一张图片是否适合作为日报配图。"""

    name = "verify_image"
    description = (
        "验证一张图片是否适合作为日报配图。"
        "检查：是否与文章相关、是否是真实内容图（非logo/验证码）、"
        "是否有版权问题。验证通过才能放入日报。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "image_url": {"type": "string", "description": "图片 URL"},
            "article_url": {"type": "string", "description": "关联文章 URL"},
            "context": {
                "type": "string",
                "description": "文章主题描述（用于相关性判断）",
            },
        },
        "required": ["image_url", "context"],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        image_url: str = normalize_external_url(kwargs.get("image_url", "").strip())
        article_url: str = normalize_external_url(kwargs.get("article_url", ""))
        context: str = kwargs.get("context", "")

        if not image_url:
            return ToolResult(success=False, summary="image_url 不能为空", data={})

        # 基础规则检查（不需要 LLM）
        reject_reason = self._rule_check(image_url)
        if reject_reason:
            return ToolResult(
                success=True,
                summary=f"❌ 图片不合格: {reject_reason}\n{image_url}",
                data={"suitable": False, "reason": reject_reason},
            )

        # LLM 视觉验证（如果支持）
        if self._llm and self._llm.enabled:
            system_prompt = (
                "你是日报图片编辑。判断这张图片是否适合作为日报配图。\n"
                "输出 JSON：{suitable: bool, reason: str}\n"
                "合格条件：真实内容图、与主题相关、非logo/验证码/装饰图"
            )
            user_content = f"文章主题: {context}\n图片URL: {image_url}"
            result = await self._llm.simple_json_completion(system_prompt, user_content)
            suitable = bool(result.get("suitable", True))
            reason = result.get("reason", "")
        else:
            # 无 LLM 时，通过规则检查的都视为合格
            suitable = True
            reason = "基础规则检查通过"

        if suitable and article_url:
            memory.mark_image_verified(article_url, image_url, reason)

        status = "✅ 图片合格" if suitable else "❌ 图片不合格"
        return ToolResult(
            success=True,
            summary=f"{status}: {reason}\n{image_url}",
            data={"suitable": suitable, "reason": reason, "image_url": image_url},
        )

    @staticmethod
    def _rule_check(image_url: str) -> str | None:
        """基础规则检查，返回拒绝原因或 None。"""
        url_lower = image_url.lower()
        if any(
            p in url_lower
            for p in ["logo", "icon", "banner", "captcha", "ads", "advertisement"]
        ):
            return "疑似 logo/图标/广告图"
        if any(ext in url_lower for ext in [".gif"]):
            return "GIF 动图不适合日报"
        if len(image_url) < 10:
            return "无效图片 URL"
        return None


# ── 8. write_section ─────────────────────────────────────


class WriteSectionTool(Tool):
    """基于收集到的证据写一个板块的内容。"""

    name = "write_section"
    description = (
        "基于已收集的文章，写日报的某个板块内容（markdown 格式）。"
        "先用 check_coverage 确认板块有足够文章再调用。"
        "输出带来源引用的 markdown 文本。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": ["industry", "policy", "academic"],
                "description": "板块名称",
            },
            "target_count": {
                "type": "integer",
                "description": "目标条目数（1-3），默认 2",
            },
        },
        "required": ["section"],
    }

    _SECTION_HEADINGS = {
        "academic": "## 🔬 A. 前沿技术与学术",
        "industry": "## 🏭 B. 产业动态与设备",
        "policy": "## 📢 C. 下游应用与政策",
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        section: str = kwargs.get("section", "industry")
        target_count: int = int(kwargs.get("target_count", 2))

        topics = memory.get_compiled_topics(section)
        if not topics:
            return ToolResult(
                success=False,
                summary=f"板块 '{section}' 没有足够高可信主题",
                data={"section": section, "article_count": 0, "topic_count": 0},
            )

        topics = topics[:target_count]
        heading = self._SECTION_HEADINGS.get(section, f"## {section}")
        content = ""

        if self._llm and self._llm.enabled:
            topics_payload = "\n\n".join(
                [
                    "\n".join(
                        [
                            f"主题: {topic['title']}",
                            f"来源等级: {topic['source_tier']} / {topic['source_reliability_label']}",
                            f"证据强度: {topic['evidence_strength']}",
                            f"允许数字: {'是' if topic['supports_numeric_claims'] else '否'}",
                            "事实句:",
                            *[f"- {fact}" for fact in topic.get("facts", []) if fact],
                            "引用:",
                            *[
                                f"- {citation['title']} | {citation['domain']} | {citation['source_tier']} | {citation['url']}"
                                for citation in topic.get("citations", [])
                            ],
                        ]
                    )
                    for topic in topics
                ]
            )
            section_labels = {
                "academic": "学术前沿",
                "industry": "产业动态",
                "policy": "政策与下游应用",
            }
            system_prompt = (
                "你是证据优先的高分子材料加工日报编辑。\n"
                f"基于以下规则层已批准的主题，撰写日报「{section_labels.get(section, section)}」板块。\n"
                "硬性规则：\n"
                "1. 只能使用提供的事实句与引用，不得补充任何新数字、占比、市场规模、年份、时间线。\n"
                "2. 不得输出星级、信号强度、预计、将超过、有望、引导全年等外推性措辞。\n"
                "3. 可靠度只能照抄给定的来源等级标签，不得自创高/中/低判断。\n"
                "4. 若主题证据不足以支撑行业影响分析，只写事实摘要与谨慎观察，不要拔高。\n"
                "5. 每个主题最后必须带来源引用，格式为 [来源名称](URL)。\n"
                "6. 不要生成行业趋势综述，不要跨主题补数。\n"
                "输出纯 markdown，不要代码块。"
            )
            try:
                content = await asyncio.wait_for(
                    self._llm.simple_completion(
                        system_prompt, topics_payload, temperature=0.2
                    ),
                    timeout=25.0,
                )
                memory.record_section_generation(section, "llm")
            except asyncio.TimeoutError:
                memory.record_section_generation(
                    section, "template_fallback", timed_out=True
                )
                content = self._render_safe_section_template(heading, topics)
            except Exception:
                memory.record_section_generation(section, "template_fallback")
                content = self._render_safe_section_template(heading, topics)
        if not content:
            memory.record_section_generation(section, "template_fallback")
            content = self._render_safe_section_template(heading, topics)

        if not content.startswith("#"):
            content = f"{heading}\n\n{content}"

        summary = f"已写 {section} 板块（{len(topics)} 个主题）"

        # 关键：将写好的内容缓存到 WorkingMemory，让 _build_result 能收集
        memory.cache_section_content(section, content)

        return ToolResult(
            success=True,
            summary=summary,
            data={"section": section, "content": content, "topic_count": len(topics)},
        )

    @staticmethod
    def _render_safe_section_template(
        heading: str, topics: list[dict[str, Any]]
    ) -> str:
        lines = [heading, ""]
        for index, topic in enumerate(topics, start=1):
            citation_lines = "；".join(
                f"[{citation['domain']}]({citation['url']})"
                for citation in topic.get("citations", [])[:2]
            )
            fact_text = "；".join(fact for fact in topic.get("facts", [])[:2] if fact)
            observation = (
                "谨慎观察：当前素材支持该主题具备持续关注价值，但不足以做更强外推。"
            )
            if topic.get("supports_numeric_claims"):
                observation = (
                    "谨慎观察：当前素材包含可引用的事实数据，可作为后续跟踪依据。"
                )
            lines.extend(
                [
                    f"### {index}. {topic['title']}",
                    fact_text or "事实摘要：暂无更完整原文事实句。",
                    observation,
                    f"来源等级：{topic['source_reliability_label']}；证据强度：{topic['evidence_strength']}；参考：{citation_lines}",
                    "",
                ]
            )
        return "\n".join(lines).strip()


# ── 9. check_coverage ────────────────────────────────────

# 搜索盲区话题模板
_BLIND_SPOT_TOPICS: list[tuple[list[str], str]] = [
    (["价格", "行情", "涨价", "跌价", "树脂"], "原料价格/市场行情"),
    (["并购", "重组", "收购", "合作", "合资"], "企业并购/战略合作"),
    (["投产", "扩产", "新建", "产能", "工厂"], "产能扩建/新工厂"),
    (["汽车", "轻量化", "新能源车"], "汽车轻量化应用"),
    (["电子", "封装", "半导体", "芯片"], "电子封装应用"),
    (["医疗", "器械", "生物相容"], "医疗器械应用"),
    (["包装", "食品", "接触材料"], "包装/食品接触材料"),
    (["碳关税", "CBAM", "碳足迹", "ESG"], "碳关税/ESG"),
    (["K展", "Chinaplas", "展会", "博览会"], "行业展会"),
    (["标准", "ISO", "ASTM", "国标", "GB/T"], "标准更新"),
]


def _suggest_blind_spots(memory: "WorkingMemory", suggestions: list[str]) -> None:
    """基于已搜索内容检测盲区并建议补充搜索。"""
    searched_text = " ".join(memory.searched_queries).lower()

    blind_spots = []
    for keywords, label in _BLIND_SPOT_TOPICS:
        if not any(kw.lower() in searched_text for kw in keywords):
            blind_spots.append(label)

    if blind_spots:
        examples = blind_spots[:4]
        suggestions.append(
            f"盲区提醒：尚未覆盖的话题方向 — {'、'.join(examples)}。请补充搜索。"
        )


class CheckCoverageTool(Tool):
    """检查当前收集状态，发现缺口并给出建议。"""

    name = "check_coverage"
    description = (
        "检查当前已收集的文章和图片状态，发现缺口，"
        "判断是否已经可以写报告或需要继续探索。"
        "每收集 3-4 篇文章后调用一次，以及写报告前调用。"
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        coverage = memory.coverage
        articles = memory.publishable_articles()
        gaps = coverage.gaps()

        status_lines = [
            f"当前覆盖状态：",
            f"  产业动态: {coverage.industry_count} 条",
            f"  政策标准: {coverage.policy_count} 条",
            f"  学术前沿: {coverage.academic_count} 条",
            f"  正式主题: {coverage.formal_topic_count} 条",
            f"  已验证图片: {coverage.verified_image_count} 张",
            f"  共 {len(articles)} 篇可发布文章，{coverage.section_count} 个板块",
        ]

        if coverage.is_complete:
            status_lines.append("\n✅ 覆盖完整（可发布 complete 级日报）")
            suggestions = ["可以调用 finish 完成报告了"]
            ready = True
        elif coverage.is_publishable:
            status_lines.append("\n⚠️ 基本达标（可发布 partial 级日报）")
            if gaps:
                status_lines.append(f"缺口: {', '.join(gaps)}")
            suggestions = []
            if coverage.verified_image_count < 2:
                suggestions.append("建议用 search_images 给主要文章找配图")
            if (coverage.formal_topic_count or len(articles)) < 4:
                suggestions.append("可继续补充高可信主题以达到 complete 级")
            else:
                suggestions.append("可以调用 finish 完成报告，或继续完善")
            ready = True
        else:
            status_lines.append("\n❌ 内容不足，需要继续探索")
            suggestions = []
            if gaps:
                for gap in gaps:
                    if "产业" in gap:
                        suggestions.append(
                            "建议搜索产业动态：如 '\"注塑机\" 新品发布', '\"生物基材料\" 产业化', '\"轮胎\" 涨价 扩产'"
                        )
                    elif "政策" in gap:
                        suggestions.append(
                            "建议搜索政策标准：如 '\"以旧换新\" 塑料回收', '\"欧盟\" 碳关税 塑料', '\"国标\" 橡胶检测'"
                        )
                    elif "学术" in gap:
                        suggestions.append(
                            "建议搜索学术方向：如 '\"微纳米层叠\" 最新应用', '\"静电纺丝\" 产业化', 'polymer processing latest research'"
                        )
                    elif "图片" in gap:
                        suggestions.append("用 search_images 为主要文章找配图")

            # 基于已搜内容发现盲区
            _suggest_blind_spots(memory, suggestions)

            if len(memory.searched_queries) < 8:
                suggestions.append(
                    f"进度提醒：你目前只搜索了 {len(memory.searched_queries)} 次。请确保完成 8 轮不同维度的广泛搜索后再结束。"
                )
            ready = False

        if memory.exploration_queue and not ready:
            suggestions.append(
                f"探索队列还有 {len(memory.exploration_queue)} 条线索待处理"
            )
        elif memory.exploration_queue and ready:
            suggestions.append("已有足够素材，建议直接写稿，不必继续处理剩余探索线索")

        summary = "\n".join(status_lines)
        if suggestions:
            summary += "\n\n建议下一步：\n" + "\n".join(f"- {s}" for s in suggestions)

        return ToolResult(
            success=True,
            summary=summary,
            data={
                "coverage": coverage.to_dict(),
                "ready_to_finish": ready,
                "gaps": gaps,
                "suggestions": suggestions,
            },
        )


# ── 10. finish ───────────────────────────────────────────


class FinishTool(Tool):
    """完成报告，生成最终输出。"""

    name = "finish"
    description = (
        "完成报告生成，输出最终的报告内容。"
        "必须在所有需要的 write_section 完成后调用。"
        "调用此工具后 Agent 将停止探索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "日报标题",
            },
            "summary": {
                "type": "string",
                "description": "本次日报的一句话摘要",
            },
            "sections_content": {
                "type": "object",
                "description": "各板块内容，key 为 industry/policy/academic，value 为 markdown 内容",
            },
        },
        "required": ["title"],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        title: str = kwargs.get("title", "高分子加工全视界日报")
        summary_text: str = kwargs.get("summary", "")
        sections_content: dict[str, str] = kwargs.get("sections_content", {})

        articles = memory.publishable_articles()
        if not articles:
            return ToolResult(
                success=False,
                summary="没有足够的文章内容，无法完成报告",
                data={"ready": False},
            )

        # ── 生成"编者按"洞察摘要 ──
        editorial = ""
        trusted_findings = [
            a.key_finding
            for a in articles
            if a.key_finding
            and a.source_tier in {"A", "B"}
            and not a.requires_observation_only
        ]
        if trusted_findings:
            editorial = "今日关注：" + "；".join(trusted_findings[:3]) + "。"

        return ToolResult(
            success=True,
            summary=f"✅ 报告完成：{title}（{len(articles)} 篇文章）",
            data={
                "title": title,
                "summary": summary_text,
                "editorial": editorial,
                "sections_content": sections_content,
                "articles": [a.to_dict() for a in articles],
                "coverage": memory.coverage.to_dict(),
                "is_finish": True,
            },
        )


# ── Factory ───────────────────────────────────────────────


def build_all_tools(
    brave_client: Any = None,
    scraper_client: Any = None,
    zhipu_client: Any = None,
    llm_client: "LLMClient | None" = None,
    scrape_timeout_seconds: int | None = None,
) -> list[Tool]:
    """构建完整工具集。"""
    return [
        WebSearchTool(brave_client=brave_client, zhipu_client=zhipu_client),
        ReadPageTool(
            scraper_client=scraper_client, timeout_seconds=scrape_timeout_seconds
        ),
        FollowReferencesTool(),
        EvaluateArticleTool(llm_client=llm_client),
        CompareSourcesTool(llm_client=llm_client),
        SearchImagesTool(brave_client=brave_client, scraper_client=scraper_client),
        VerifyImageTool(llm_client=llm_client),
        WriteSectionTool(llm_client=llm_client),
        CheckCoverageTool(),
        FinishTool(llm_client=llm_client),
    ]
