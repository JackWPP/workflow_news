from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.config import settings
from app.utils import extract_domain, parse_datetime

logger = logging.getLogger(__name__)

# HTML meta extraction patterns
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


def _extract_html_meta(html: str) -> tuple[str, str | None, Any]:
    """从原始 HTML 中提取 title、og:image、发布日期。"""
    # title
    m = _TITLE_RE.search(html)
    title = m.group(1).strip() if m else ""

    # og:image — try both attribute orderings
    image_url: str | None = None
    m = _OG_IMAGE_RE.search(html) or _OG_IMAGE_RE_ALT.search(html)
    if m:
        image_url = m.group(1).strip()

    # published time — try both attribute orderings
    published_at = None
    m = _ARTICLE_PUBLISHED_RE.search(html) or _ARTICLE_PUBLISHED_RE_ALT.search(html)
    if m:
        published_at = parse_datetime(m.group(1).strip())

    return title, image_url, published_at


class ScraperClient:
    """三层降级抓取：Trafilatura → Jina Reader → direct_http。

    每层独立失败，绝不级联。
    """

    def __init__(
        self,
        jina_client: Any = None,
        browser_fallback: Any = None,
    ) -> None:
        # Lazy import to avoid circular dependency at module level
        if jina_client is not None:
            self._jina = jina_client
        else:
            from app.services.jina_reader import JinaReaderClient

            self._jina = JinaReaderClient()
        self._browser_fallback = browser_fallback

    @property
    def enabled(self) -> bool:
        return True  # Trafilatura 始终可用

    async def scrape(
        self, url: str, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        timeout = timeout_seconds or settings.scrape_timeout_seconds

        if self._prefer_jina_first(url):
            try:
                result = await self._jina.scrape(url, timeout_seconds=timeout)
                if result.get("status") != "error" and result.get("markdown"):
                    logger.debug("scraper: Jina-first success for %s", url)
                    return result
            except Exception as exc:
                logger.debug("scraper: Jina-first failed for %s: %s", url, exc)

        # 第一层：Trafilatura（本地，最快）
        try:
            result = await self._trafilatura_scrape(url)
            if result and result.get("markdown"):
                logger.debug("scraper: Trafilatura success for %s", url)
                return result
        except Exception as exc:
            logger.debug("scraper: Trafilatura failed for %s: %s", url, exc)

        # 第二层：Jina Reader
        try:
            result = await self._jina.scrape(url, timeout_seconds=timeout)
            if result.get("status") != "error" and result.get("markdown"):
                logger.debug("scraper: Jina success for %s", url)
                return result
        except Exception as exc:
            logger.debug("scraper: Jina failed for %s: %s", url, exc)

        # 第三层：direct_http fallback
        try:
            result = await self._jina._fallback_scrape(url, timeout)
            logger.debug("scraper: httpx fallback for %s", url)
            return result
        except Exception as exc:
            logger.warning("scraper: all layers failed for %s: %s", url, exc)
            return {
                "url": url,
                "resolved_url": url,
                "domain": extract_domain(url),
                "title": "",
                "markdown": "",
                "html": "",
                "metadata": {},
                "image_url": None,
                "published_at": None,
                "status": "error",
                "error": str(exc),
                "scrape_layer": "none",
            }

    @staticmethod
    def _prefer_jina_first(url: str) -> bool:
        domain = extract_domain(url)
        return any(
            domain == candidate or domain.endswith(f".{candidate}")
            for candidate in _JINA_FIRST_DOMAINS
        )

    async def _trafilatura_scrape(self, url: str) -> dict[str, Any] | None:
        """用 Trafilatura 提取正文+内联图片，用 HTML meta 补充 title/og:image/date。"""
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
        if not markdown:
            return None

        # 从 HTML 补充 metadata
        title, image_url, published_at = _extract_html_meta(downloaded)

        # 如果 HTML meta 没有日期，尝试从文本中提取
        if published_at is None:
            published_at = _extract_datetime_from_text(markdown)

        return {
            "url": url,
            "resolved_url": url,
            "domain": extract_domain(url),
            "title": title,
            "markdown": markdown,
            "html": "",
            "metadata": {},
            "image_url": image_url,
            "published_at": published_at,
            "status": "success",
            "scrape_layer": "trafilatura",
        }


def _extract_datetime_from_text(text: str):
    """从正文中用正则提取发布日期。"""
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
