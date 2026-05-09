from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.utils import extract_domain, normalize_external_url, parse_datetime

logger = logging.getLogger(__name__)

_OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)
_ARTICLE_PUBLISHED_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_ARTICLE_PUBLISHED_RE_ALT = re.compile(
    r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\']article:published_time["\']',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_JINA_FIRST_DOMAINS = (
    "toutiao.com",
    "qq.com",
    "weixin.qq.com",
    "mp.weixin.qq.com",
    "36kr.com",
    "baijiahao.baidu.com",
)

_KEYWORDS_LOWER: list[str] = [
    "高分子", "塑料", "橡胶", "复合材料", "树脂", "改性", "薄膜", "包装",
    "注塑", "挤出", "吹塑", "成型", "回收", "再生", "生物基", "降解",
    "polymer", "plastic", "rubber", "composite", "resin", "recycling",
    "biodegradable", "processing", "injection molding", "extrusion",
    "additive manufacturing",
    "发现", "突破", "进展", "研究", "技术", "创新",
]


@dataclass
class ExtractResult:
    url: str
    success: bool
    title: str = ""
    content: str = ""
    markdown: str = ""
    domain: str = ""
    published_at: str | None = None
    extraction_method: str = ""
    error: str | None = None


class ContentExtractor:

    def __init__(self, scraper_client=None, jina_client=None, timeout=20):
        self._scraper = scraper_client
        self._jina = jina_client
        self._timeout = timeout

    async def extract(self, url: str, title_hint: str = "") -> ExtractResult:
        normalized = normalize_external_url(url)
        if not normalized:
            return ExtractResult(url=url, success=False, error="url 不能为空")

        domain = extract_domain(normalized)

        if self._prefer_jina_first(normalized):
            result = await self._try_jina(normalized, domain, title_hint)
            if result:
                return result

        result = await self._try_trafilatura(normalized, domain, title_hint)
        if result:
            return result

        result = await self._try_jina(normalized, domain, title_hint)
        if result:
            return result

        return await self._try_direct_http(normalized, domain, title_hint)

    async def extract_batch(
        self, urls: list[str], concurrency: int = 3,
    ) -> list[ExtractResult]:
        sem = asyncio.Semaphore(concurrency)

        async def _one(url: str) -> ExtractResult:
            async with sem:
                return await self.extract(url)

        return await asyncio.gather(*[_one(u) for u in urls])

    async def _try_trafilatura(
        self, url: str, domain: str, title_hint: str,
    ) -> ExtractResult | None:
        try:
            import trafilatura

            downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
            if not downloaded:
                return None

            markdown = await asyncio.to_thread(
                trafilatura.extract,
                downloaded,
                output_format="markdown",
                include_images=True,
                include_links=True,
                favor_precision=False,
                include_formatting=True,
            )
            if not markdown or not markdown.strip():
                return None

            title, _image_url, published_dt = _extract_html_meta(downloaded)
            if published_dt is None:
                published_dt = _extract_datetime_from_text(markdown)

            content = self._extract_content(markdown)

            return ExtractResult(
                url=url,
                success=True,
                title=title or title_hint,
                content=content,
                markdown=markdown,
                domain=domain,
                published_at=published_dt.isoformat() if published_dt else None,
                extraction_method="trafilatura",
            )
        except Exception as exc:
            logger.debug("trafilatura failed for %s: %s", url, exc)
            return None

    async def _try_jina(
        self, url: str, domain: str, title_hint: str,
    ) -> ExtractResult | None:
        jina = self._jina
        if jina is None:
            from app.services.jina_reader import JinaReaderClient
            jina = JinaReaderClient()

        try:
            result = await jina.scrape(url, timeout_seconds=self._timeout)
            if result.get("status") == "error" or not result.get("markdown"):
                return None

            markdown: str = result.get("markdown", "")
            title: str = result.get("title", "")
            published_dt = result.get("published_at")

            content = self._extract_content(markdown)

            return ExtractResult(
                url=url,
                success=True,
                title=title or title_hint,
                content=content,
                markdown=markdown,
                domain=domain,
                published_at=published_dt.isoformat() if published_dt else None,
                extraction_method="jina",
            )
        except Exception as exc:
            logger.debug("jina failed for %s: %s", url, exc)
            return None

    async def _try_direct_http(
        self, url: str, domain: str, title_hint: str,
    ) -> ExtractResult:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True, headers=headers,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text

            title = domain
            m = _TITLE_RE.search(html)
            if m:
                title = m.group(1).strip()

            published_dt = None
            m = _ARTICLE_PUBLISHED_RE.search(html) or _ARTICLE_PUBLISHED_RE_ALT.search(html)
            if m:
                published_dt = parse_datetime(m.group(1).strip())

            text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            markdown = text[:8000]

            if published_dt is None:
                published_dt = _extract_datetime_from_text(markdown)

            content = self._extract_content(markdown)

            return ExtractResult(
                url=url,
                success=True,
                title=title or title_hint,
                content=content,
                markdown=markdown,
                domain=domain,
                published_at=published_dt.isoformat() if published_dt else None,
                extraction_method="http",
            )
        except Exception as exc:
            logger.warning("direct http failed for %s: %s", url, exc)
            return ExtractResult(
                url=url,
                success=False,
                domain=domain,
                extraction_method="http",
                error=str(exc),
            )

    @staticmethod
    def _prefer_jina_first(url: str) -> bool:
        domain = extract_domain(url)
        return any(
            domain == candidate or domain.endswith(f".{candidate}")
            for candidate in _JINA_FIRST_DOMAINS
        )

    @staticmethod
    def _extract_content(markdown: str, max_chars: int = 8000) -> str:
        if len(markdown) <= max_chars:
            return markdown

        head_size = 2500
        tail_size = 1500
        mid_budget = max_chars - head_size - tail_size

        head = markdown[:head_size]
        tail = markdown[-tail_size:] if len(markdown) > tail_size else ""

        mid_text = markdown[head_size : len(markdown) - tail_size]
        paragraphs = [p for p in mid_text.split("\n\n") if len(p.strip()) > 50]

        if mid_budget <= 0 or not paragraphs:
            return head + "\n\n...\n\n" + tail

        def _density(text: str) -> float:
            text_lower = text.lower()
            hits = sum(1 for kw in _KEYWORDS_LOWER if kw in text_lower)
            return hits / max(len(text), 1) * 1000

        scored = [(_density(p), p) for p in paragraphs]
        scored.sort(key=lambda x: x[0], reverse=True)

        mid_selected: list[str] = []
        mid_len = 0
        for _, paragraph in scored:
            if mid_len + len(paragraph) > mid_budget:
                break
            mid_selected.append(paragraph)
            mid_len += len(paragraph)

        result = head
        if mid_selected:
            result += "\n\n...[中间重点段落]...\n\n" + "\n\n".join(mid_selected)
        result += "\n\n...[文末]...\n\n" + tail
        return result


def _extract_html_meta(html: str) -> tuple[str, str | None, Any]:
    m = _TITLE_RE.search(html)
    title = m.group(1).strip() if m else ""

    image_url: str | None = None
    m = _OG_IMAGE_RE.search(html) or _OG_IMAGE_RE_ALT.search(html)
    if m:
        image_url = m.group(1).strip()

    published_at = None
    m = _ARTICLE_PUBLISHED_RE.search(html) or _ARTICLE_PUBLISHED_RE_ALT.search(html)
    if m:
        published_at = parse_datetime(m.group(1).strip())

    return title, image_url, published_at


def _extract_datetime_from_text(text: str):
    if not text:
        return None
    patterns = [
        r"(?:日期|发布时间|發佈時間|發佈日期|发布于)\s*[：:]\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
        r"(20\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)",
        r"(20\d{2}[-/]\d{2}[-/]\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).replace("年", "-").replace("月", "-").replace("日", "")
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    return None
