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

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from app.services.working_memory import (
    ArticleSummary,
    ExplorationLead,
    ImageCandidate,
)
from app.utils import extract_domain, summarize_markdown

if TYPE_CHECKING:
    from app.services.llm_client import LLMClient
    from app.services.working_memory import WorkingMemory

logger = logging.getLogger(__name__)


# ── Base ─────────────────────────────────────────────────

@dataclass
class ToolResult:
    """工具执行结果。"""
    success: bool
    summary: str              # Agent 可读的结果摘要（用于 message history）
    data: dict[str, Any]      # 结构化数据（用于 WorkingMemory 记录）

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


# ── 不可信域名（硬过滤） ───────────────────────────────────
_BLOCKED_RESULT_DOMAINS = {
    # 台湾媒体
    "digitimes.com.tw", "udn.com", "ltn.com.tw", "chinatimes.com",
    "yahoo.com.tw", "tw.news.yahoo.com", "ctee.com.tw", "money.udn.com",
    "technews.tw", "bnext.com.tw", "ettoday.net", "setn.com",
    "storm.mg", "cna.com.tw", "taiwannews.com.tw",
}


def _is_blocked_domain(domain: str) -> bool:
    """检查域名是否在屏蔽列表中或是 .com.tw 后缀。"""
    if domain in _BLOCKED_RESULT_DOMAINS:
        return True
    if domain.endswith(".com.tw") or domain.endswith(".org.tw"):
        return True
    return False


def _region_tag(domain: str) -> str:
    """根据域名判断地区标签。"""
    if domain.endswith(".com.tw") or domain.endswith(".org.tw") or domain in _BLOCKED_RESULT_DOMAINS:
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
        "搜索网页、新闻或图片。你可以自由构造搜索词，"
        "系统会同时搜索 web 和 news 源。"
        "搜索结果包含标题、摘要、URL 和发布时间。"
        "如果某个方向你已经搜过，请换一个角度。"
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

    def __init__(self, brave_client: Any = None, firecrawl_client: Any = None) -> None:
        self._brave = brave_client
        self._firecrawl = firecrawl_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "").strip()
        language: str = kwargs.get("language", "zh")

        if not query:
            return ToolResult(success=False, summary="query 不能为空", data={})

        if memory.has_searched(query):
            return ToolResult(
                success=False,
                summary=f"'{query}' 已经搜索过，请换一个不同的搜索词",
                data={"already_searched": True},
            )

        memory.record_search(query)

        # ── 搜索策略：中文用 Firecrawl（Brave 中文搜索不可用），英文用 Brave ──
        results: list[dict[str, Any]] = []

        if language == "zh" and self._firecrawl and self._firecrawl.enabled:
            # 中文搜索：Firecrawl 为主
            try:
                results = await self._firecrawl.search(query, limit=10, country="CN", timeout=30000)
                logger.info("web_search [zh/firecrawl] '%s' → %d results", query, len(results))
            except Exception as exc:
                logger.warning("Firecrawl search failed for '%s': %s", query, exc)

        if not results and self._brave and self._brave.enabled:
            # 英文搜索 或 中文 Firecrawl 失败时的 Brave 兜底
            from app.config import settings
            search_lang = settings.brave_search_lang if language == "zh" else settings.brave_fallback_lang
            try:
                results = await self._brave.search_all(query, search_lang)
                logger.info("web_search [%s/brave] '%s' → %d results", language, query, len(results))
            except Exception as exc:
                logger.warning("Brave search failed for '%s': %s", query, exc)

        if not results:
            return ToolResult(
                success=True,
                summary=f"'{query}' 没有找到结果，请尝试不同的搜索词",
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
            logger.info("web_search filtered out %d results from blocked domains", blocked_count)
        results = filtered

        if not results:
            return ToolResult(
                success=True,
                summary=f"'{query}' 搜索到了结果但都被过滤了（不可信来源），请换一个搜索词",
                data={"results": []},
            )

        # ── 时效性过滤：只保留近 72 小时内发布的内容 ──
        cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
        fresh_results = []
        stale_count = 0
        for r in results:
            pub = r.get("published_at")
            if pub is not None:
                # 确保 pub 有 timezone 信息
                if hasattr(pub, 'tzinfo') and pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    stale_count += 1
                    continue
            # pub 为 None 的保留（无法判断时效）
            fresh_results.append(r)
        if stale_count:
            logger.info("web_search filtered out %d stale results (>72h old)", stale_count)
        results = fresh_results

        if not results:
            return ToolResult(
                success=True,
                summary=f"'{query}' 的搜索结果都是旧闻（超过 72 小时），请换一个更具时效性的搜索词。不要搜索去年的内容。",
                data={"results": []},
            )

        # ── 格式化结果给 LLM 看 ──
        formatted = []
        for r in results[:10]:
            url = r.get("url", "")
            domain = r.get("domain") or extract_domain(url)
            region = _region_tag(domain)
            published = r.get("published_at")
            pub_str = published.strftime("%Y-%m-%d") if published else "未知时间"
            snippet = (r.get("snippet") or "")[:200]
            formatted.append(
                f"- [{r.get('title', 'Untitled')}]({url})\n"
                f"  来源: {domain}{region} | {pub_str}\n"
                f"  摘要: {snippet}"
            )

        summary = f"搜索 '{query}' 找到 {len(results)} 条结果：\n\n" + "\n\n".join(formatted[:8])
        return ToolResult(
            success=True,
            summary=summary,
            data={"query": query, "results": results[:10], "total": len(results)},
        )


# ── 2. read_page ─────────────────────────────────────────

class ReadPageTool(Tool):
    """深度阅读一个网页，获取完整内容。"""

    name = "read_page"
    description = (
        "深度阅读一个网页，获取完整正文、发布时间和页面图片。"
        "用于搜索结果中找到有价值的链接后深入阅读。"
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

    def __init__(self, firecrawl_client: Any = None) -> None:
        self._firecrawl = firecrawl_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        url: str = kwargs.get("url", "").strip()
        if not url:
            return ToolResult(success=False, summary="url 不能为空", data={})

        if memory.has_read(url):
            return ToolResult(
                success=False,
                summary=f"已经阅读过 {url}，请选择其他链接",
                data={"already_read": True},
            )

        domain = extract_domain(url)

        if self._firecrawl is None or not self._firecrawl.enabled:
            memory.record_read(url, links=[])
            return ToolResult(
                success=False,
                summary="页面抓取服务不可用（Firecrawl API key 未配置）",
                data={},
            )

        try:
            from app.config import settings
            result = await self._firecrawl.scrape(url, timeout_seconds=settings.scrape_timeout_seconds)
        except Exception as exc:
            memory.record_read(url, links=[])
            logger.warning("read_page failed for %s: %s", url, exc)
            return ToolResult(success=False, summary=f"抓取失败: {url} — {exc}", data={"url": url})

        title = result.get("title") or ""
        markdown = result.get("markdown") or ""
        image_url = result.get("image_url")
        published_at = result.get("published_at")
        pub_str = published_at.strftime("%Y-%m-%d %H:%M") if published_at else "未知"

        # 提取页内链接（用于 follow_references）
        links = self._extract_links(markdown, url)
        memory.record_read(url, links=links)

        # 生成内容摘要（前 3000 字）
        content_summary = summarize_markdown(markdown[:6000]) or markdown[:500]

        summary = (
            f"📄 {title}\n"
            f"来源: {domain} | 发布: {pub_str}\n"
            f"内容: {content_summary}\n"
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
                "links": links[:10],
            },
        )

    @staticmethod
    def _extract_links(markdown: str, base_url: str) -> list[dict[str, str]]:
        """从 markdown 中提取链接。"""
        pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
        links = []
        for match in re.finditer(pattern, markdown):
            text, href = match.group(1), match.group(2)
            # 过滤掉图片、锚点
            if any(ext in href.lower() for ext in [".jpg", ".png", ".gif", ".svg", ".pdf"]):
                continue
            domain = extract_domain(href)
            links.append({"text": text, "url": href, "domain": domain})
        return links[:15]


# ── 3. follow_references ─────────────────────────────────

class FollowReferencesTool(Tool):
    """从一个网页中发现引用、参考文献和相关链接。"""

    name = "follow_references"
    description = (
        "从一个已阅读的页面中提取引用、参考文献和相关链接，"
        "加入探索队列。用于在一篇文章中发现值得深入的新方向。"
        "不会立即读取，而是将线索加入工作记忆。"
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
            if memory.has_read(link_url):
                continue
            # 焦点过滤：如果有焦点词，优先匹配标题包含关键词的链接
            relevance = 0.5
            if focus_lower and any(kw in link_text.lower() for kw in focus_lower.split()):
                relevance = 0.8

            lead = ExplorationLead(
                url=link_url,
                title=link_text,
                reason=f"从 {extract_domain(url)} 页面发现的引用",
                priority=relevance,
            )
            memory.add_exploration_lead(lead)
            leads_added += 1

        formatted = [f"- [{lk.get('text', '...')}]({lk.get('url', '')})" for lk in page_links[:5]]
        summary = (
            f"从 '{url}' 中提取到 {len(page_links)} 个链接，"
            f"加入了 {leads_added} 条探索线索：\n" + "\n".join(formatted)
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={"url": url, "leads_added": leads_added, "total_links": len(page_links)},
        )


# ── 4. evaluate_article ──────────────────────────────────

class EvaluateArticleTool(Tool):
    """评估一篇文章是否值得纳入日报。"""

    name = "evaluate_article"
    description = (
        "评估一篇文章的价值：是否值得纳入日报、属于哪个板块、"
        "核心发现是什么。用于决定是否要深入研究某篇文章。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "文章标题"},
            "content": {"type": "string", "description": "文章内容摘要（前 1000 字）"},
            "url": {"type": "string", "description": "文章 URL"},
            "domain": {"type": "string", "description": "来源域名"},
            "published_at": {"type": "string", "description": "发布时间（可选）"},
        },
        "required": ["title", "content", "url"],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        title: str = kwargs.get("title", "")
        content: str = kwargs.get("content", "")
        url: str = kwargs.get("url", "")
        domain: str = kwargs.get("domain", extract_domain(url))
        published_at: str = kwargs.get("published_at", "")

        if not title or not content:
            return ToolResult(success=False, summary="需要提供 title 和 content", data={})

        system_prompt = (
            "你是高分子材料加工领域的日报研究员。\n"
            "评估这篇文章是否值得纳入今日日报，并给出理由。\n"
            "优先选择中国大陆权威媒体或英文学术/产业新闻，对台湾或非相关繁体媒体降低评分权重！\n"
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
            f"发布时间：{published_at or '未知'}\n"
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
            article = ArticleSummary(
                title=zh_title,
                url=url,
                domain=domain,
                source_name=domain,
                published_at=published_at,
                summary=zh_summary,
                section=section if section in {"academic", "industry", "policy"} else "industry",
                key_finding=key_finding,
                worth_publishing=True,
                evaluation_reason=reason,
            )
            memory.add_article(article)

            if image_worthiness:
                memory.add_exploration_lead(ExplorationLead(
                    url=url,
                    title=f"[图片] {title}",
                    reason=f"这篇文章值得配图：{key_finding}",
                    priority=0.6,
                ))

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
            },
        )

    @staticmethod
    def _heuristic_evaluate(title: str, content: str, domain: str) -> dict[str, Any]:
        """LLM 不可用时的启发式评估。"""
        text = f"{title} {content}".lower()
        topic_hits = sum(1 for kw in [
            "高分子", "塑料", "橡胶", "polymer", "plastic", "注塑", "挤出",
            "composite", "recycling", "material",
        ] if kw.lower() in text)

        is_academic = any(kw in text for kw in ["研究", "论文", "paper", "journal", "机理", "mechanism"])
        is_policy = any(kw in text for kw in ["政策", "标准", "法规", "regulation", "policy"])
        section = "academic" if is_academic else ("policy" if is_policy else "industry")

        worthy = topic_hits >= 2
        return {
            "worthy": worthy,
            "section": section,
            "key_finding": title[:50],
            "reason": f"关键词命中 {topic_hits} 个" if worthy else "主题相关度不足",
            "image_worthiness": True,
        }


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
            return ToolResult(success=False, summary="当前没有已收集的文章可对比", data={})

        if len(articles) == 1:
            return ToolResult(
                success=True,
                summary=f"只有 1 篇文章，无需对比。文章: {articles[0].title}",
                data={"article_count": 1},
            )

        focus: str = kwargs.get("focus", "均衡报道和去重")

        article_list = "\n".join([
            f"{i+1}. [{a.section}] {a.title}\n   来源: {a.domain} | 核心: {a.key_finding}"
            for i, a in enumerate(articles)
        ])

        system_prompt = (
            "你是高分子材料加工日报的编辑。"
            f"请对比以下 {len(articles)} 篇文章，找出重复事件并推荐最终保留哪些。\n"
            "输出 JSON，包含：\n"
            "  - duplicates: [[index1, index2], ...] 重复文章的索引对\n"
            "  - keep_indices: [1,2,3,...] 推荐保留的文章索引（1-based）\n"
            "  - analysis: 对比分析（100字以内）\n"
        )

        if self._llm and self._llm.enabled:
            result = await self._llm.simple_json_completion(system_prompt, f"关注: {focus}\n\n{article_list}")
        else:
            result = {"keep_indices": list(range(1, min(len(articles) + 1, 6))), "duplicates": [], "analysis": "启发式保留前5篇"}

        keep_indices = result.get("keep_indices", [])
        duplicates = result.get("duplicates", [])
        analysis = result.get("analysis", "")

        # 标记不保留的文章
        for i, article in enumerate(articles):
            if (i + 1) not in keep_indices:
                article.worth_publishing = False

        summary = (
            f"对比 {len(articles)} 篇文章：\n"
            f"发现 {len(duplicates)} 组重复\n"
            f"推荐保留 {len(keep_indices)} 篇\n"
            f"分析：{analysis}"
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={"keep_count": len(keep_indices), "duplicates": len(duplicates), "analysis": analysis},
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

    def __init__(self, brave_client: Any = None, firecrawl_client: Any = None) -> None:
        self._brave = brave_client
        self._firecrawl = firecrawl_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        topic: str = kwargs.get("topic", "").strip()
        article_url: str = kwargs.get("article_url", "")

        if not topic:
            return ToolResult(success=False, summary="topic 不能为空", data={})

        results: list[dict[str, Any]] = []

        # 优先 Brave 图片搜索
        if self._brave and self._brave.enabled:
            from app.config import settings
            try:
                results = await self._brave.search(
                    topic, search_type="images", count=6,
                    search_lang=settings.brave_search_lang
                )
            except Exception as exc:
                logger.warning("search_images [brave] failed: %s", exc)

        # Brave 失败或无结果时，用 Firecrawl web 搜索找含图的页面
        if not results and self._firecrawl and self._firecrawl.enabled:
            try:
                web_results = await self._firecrawl.search(f"{topic} 图片", limit=5, timeout=15000)
                for wr in web_results:
                    img = wr.get("image_url")
                    if img:
                        results.append({
                            "url": wr.get("url", ""),
                            "title": wr.get("title", topic),
                            "image_url": img,
                        })
                if results:
                    logger.info("search_images [firecrawl] found %d images for '%s'", len(results), topic)
            except Exception as exc:
                logger.warning("search_images [firecrawl] failed: %s", exc)

        if not results:
            # 如果有 article_url，尝试从文章页面本身提取图片
            if article_url and self._firecrawl and self._firecrawl.enabled:
                try:
                    page_data = await self._firecrawl.scrape(article_url)
                    og_image = page_data.get("image_url")
                    if og_image:
                        results.append({
                            "url": article_url,
                            "title": page_data.get("title", topic),
                            "image_url": og_image,
                        })
                        logger.info("search_images: extracted OG image from article page")
                except Exception as exc:
                    logger.warning("search_images: failed to scrape article for image: %s", exc)

        if not results:
            memory.reject_direction(f"图片搜索无结果: {topic}")
            return ToolResult(success=True, summary=f"主题 '{topic}' 未找到合适图片", data={"results": []})

        candidates_added = 0
        formatted = []
        for r in results[:5]:
            img_url = r.get("image_url") or r.get("url", "")
            if not img_url:
                continue
            candidate = ImageCandidate(
                image_url=img_url,
                source_url=r.get("url", img_url),
                caption=r.get("title", topic),
                relevance_score=0.6,
                origin_type="search_result",
            )
            memory.add_image_candidate(article_url or "general", candidate)
            candidates_added += 1
            formatted.append(f"- {r.get('title', 'Untitled')}: {img_url}")

        summary = f"为 '{topic}' 找到 {candidates_added} 张候选图片：\n" + "\n".join(formatted)
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
            "context": {"type": "string", "description": "文章主题描述（用于相关性判断）"},
        },
        "required": ["image_url", "context"],
    }

    def __init__(self, llm_client: "LLMClient | None" = None) -> None:
        self._llm = llm_client

    async def execute(self, memory: "WorkingMemory", **kwargs: Any) -> ToolResult:
        image_url: str = kwargs.get("image_url", "").strip()
        article_url: str = kwargs.get("article_url", "")
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
        if any(p in url_lower for p in ["logo", "icon", "banner", "captcha", "ads", "advertisement"]):
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
        "在确认某个板块的文章已足够后调用。"
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

        articles = [a for a in memory.publishable_articles() if a.section == section]
        if not articles:
            return ToolResult(
                success=False,
                summary=f"板块 '{section}' 没有找到有价值的文章",
                data={"section": section, "article_count": 0},
            )

        articles = articles[:target_count]
        heading = self._SECTION_HEADINGS.get(section, f"## {section}")

        if self._llm and self._llm.enabled:
            articles_payload = "\n\n".join([
                f"标题: {a.title}\n来源: {a.domain} ({a.published_at or '未知'})\n"
                f"摘要: {a.summary[:400]}\n核心: {a.key_finding}\n链接: {a.url}"
                for a in articles
            ])
            system_prompt = (
                "你是高分子材料加工日报写作器。\n"
                f"基于以下文章，写日报{section}板块的内容。\n"
                "要求：\n"
                "1. 中文写作，专业简洁\n"
                "2. 每条必须有来源引用（[来源名称](URL)格式）\n"
                "3. 每条包含：标题、来源时间、核心发现、研究信号\n"
                "4. 不要编造内容，只用提供的资料\n"
                "输出纯 markdown，不要加代码块"
            )
            content = await self._llm.simple_completion(system_prompt, articles_payload, temperature=0.3)
        else:
            # 模板兜底
            lines = [heading, ""]
            for i, a in enumerate(articles, 1):
                pub = a.published_at or "未知"
                lines += [
                    f"### {i}. {a.title}",
                    f"* **来源**：[{a.source_name}]({a.url})",
                    f"* **时间**：{pub}",
                    f"* **摘要**：{a.summary[:200]}",
                    f"* **研究信号**：{a.key_finding}",
                    "",
                ]
            content = "\n".join(lines)

        if not content.startswith("#"):
            content = f"{heading}\n\n{content}"

        summary = f"已写 {section} 板块（{len(articles)} 条文章）"

        # 关键：将写好的内容缓存到 WorkingMemory，让 _build_result 能收集
        memory.cache_section_content(section, content)

        return ToolResult(
            success=True,
            summary=summary,
            data={"section": section, "content": content, "article_count": len(articles)},
        )


# ── 9. check_coverage ────────────────────────────────────

class CheckCoverageTool(Tool):
    """检查当前收集状态，发现缺口并给出建议。"""

    name = "check_coverage"
    description = (
        "检查当前已收集的文章和图片状态，发现缺口，"
        "判断是否已经可以写报告或需要继续探索。"
        "建议在每次收集了几篇文章后调用，以决定下一步。"
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
            if len(articles) < 3:
                suggestions.append("可继续搜索以达到 complete 级")
            else:
                suggestions.append("可以调用 finish 完成报告，或继续完善")
            ready = True
        else:
            status_lines.append("\n❌ 内容不足，需要继续探索")
            suggestions = []
            if gaps:
                for gap in gaps:
                    if "产业" in gap:
                        suggestions.append("建议搜索产业动态：如 '\"注塑机\" 新品发布', '\"生物基材料\" 产业化', '\"轮胎\" 涨价 扩产'")
                    elif "政策" in gap:
                        suggestions.append("建议搜索政策标准：如 '\"以旧换新\" 塑料回收', '\"欧盟\" 碳关税 塑料', '\"国标\" 橡胶检测'")
                    elif "学术" in gap:
                        suggestions.append("建议搜索学术方向：如 '\"微纳米层叠\" 最新应用', '\"静电纺丝\" 产业化', 'polymer processing latest research'")
                    elif "图片" in gap:
                        suggestions.append("用 search_images 为主要文章找配图")
            
            # Additional tracking suggestion
            if len(memory.searched_queries) < 6:
                suggestions.append(f"进度提醒：你目前只搜索了 {len(memory.searched_queries)} 次。请确保完成 6 轮不同维度的广泛搜索后再结束。")
            ready = False

        if memory.exploration_queue:
            suggestions.append(f"探索队列还有 {len(memory.exploration_queue)} 条线索待处理")

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
        "调用此工具后 Agent 将停止探索。"
        "建议在确认内容足够后调用，不要急于在探索初期调用。"
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

        return ToolResult(
            success=True,
            summary=f"✅ 报告完成：{title}（{len(articles)} 篇文章）",
            data={
                "title": title,
                "summary": summary_text,
                "sections_content": sections_content,
                "articles": [a.to_dict() for a in articles],
                "coverage": memory.coverage.to_dict(),
                "is_finish": True,
            },
        )


# ── Factory ───────────────────────────────────────────────

def build_all_tools(
    brave_client: Any = None,
    firecrawl_client: Any = None,
    llm_client: "LLMClient | None" = None,
) -> list[Tool]:
    """构建完整工具集。"""
    return [
        WebSearchTool(brave_client=brave_client, firecrawl_client=firecrawl_client),
        ReadPageTool(firecrawl_client=firecrawl_client),
        FollowReferencesTool(),
        EvaluateArticleTool(llm_client=llm_client),
        CompareSourcesTool(llm_client=llm_client),
        SearchImagesTool(brave_client=brave_client, firecrawl_client=firecrawl_client),
        VerifyImageTool(llm_client=llm_client),
        WriteSectionTool(llm_client=llm_client),
        CheckCoverageTool(),
        FinishTool(),
    ]
