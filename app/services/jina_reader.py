from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.config import settings
from app.utils import extract_domain, parse_datetime

# Common patterns for extracting metadata from raw HTML (used by fallback scraper)
_OG_IMAGE_RE = re.compile(r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
_ARTICLE_PUBLISHED_RE = re.compile(r'<meta\s+(?:property|name)=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _score_image_src(src: str, html: str) -> int:
    """Score an image candidate. Higher = more likely to be real article content.
    Returns -1000 to reject outright (logos, icons, tracking pixels)."""
    src_lower = src.lower()
    score = 5  # base score

    # Hard reject: tiny/tracking/UI images
    for reject in (
        "logo", "icon", "avatar", "banner", "qr", "weixin", "微信",
        "footer", "sidebar", "arrow", "pixel", "1x1", "blank",
        "loading", "spinner", "sprite", "dot", "bullet",
    ):
        if reject in src_lower:
            return -1000

    # Hard reject: common template/homepage image names
    for reject in (
        "back_home", "back-home", "logoshare", "logo-share",
        "default", "placeholder", "no-image", "noimage",
        "share_icon", "share-icon",
    ):
        if reject in src_lower:
            return -1000

    # Penalize small/social share dimensions
    for bad_size in ("32x32", "16x16", "200200", "120x120", "100x100", "50x50"):
        if bad_size in src_lower:
            score -= 15

    # Bonus for likely content images
    for good in ("content", "article", "news", "upload", "editor", "image"):
        if good in src_lower:
            score += 8

    # Bonus for larger images (common dimension hints)
    for good_size in ("1200", "1920", "800x", "x800", "700x", "x700", "large", "original"):
        if good_size in src_lower:
            score += 5

    # Bonus: image appears after <article> or <main> in HTML
    article_pos = html.lower().find("<article")
    if article_pos > 0 and src in html[article_pos:].lower():
        score += 8
    main_pos = html.lower().find("<main")
    if main_pos > 0 and src in html[main_pos:].lower():
        score += 8

    # File extension bonus
    if src_lower.endswith((".jpg", ".jpeg", ".png")):
        score += 3
    elif src_lower.endswith(".gif"):
        score -= 3  # GIFs are often decorative
    elif src_lower.endswith(".svg"):
        score -= 5  # SVGs are often logos/icons

    return score


class JinaReaderClient:
    """Scrape web pages via Jina Reader API.

    - Primary: GET https://r.jina.ai/{url}  (JSON mode)
    - Fallback: direct httpx GET + regex HTML cleanup

    No circuit breaker — each URL is independent.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or settings.jina_api_key
        self.base_url = (base_url or settings.jina_base_url).rstrip("/")
        self._skip_direct_http_domains: dict[str, float] = {}
        self._skip_ttl_seconds = 1800.0

    @property
    def enabled(self) -> bool:
        return True  # Works without API key (20 RPM free)

    async def scrape(self, url: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        timeout = timeout_seconds or settings.scrape_timeout_seconds
        try:
            return await self._jina_scrape(url, timeout)
        except Exception as exc:
            if self._should_skip_direct_http(url, exc):
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
                    "scrape_layer": "jina",
                }
        # Fallback to direct HTTP
        try:
            return await self._fallback_scrape(url, timeout)
        except Exception as exc:
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
                "scrape_layer": "direct_http",
            }

    async def _jina_scrape(self, url: str, timeout: int) -> dict[str, Any]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "X-Return-Format": "markdown",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exc: Exception | None = None
        for _ in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    response = await client.get(f"{self.base_url}/{url}", headers=headers)
                    response.raise_for_status()
                    body = response.json()
                break
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
        else:
            raise last_exc or httpx.ReadTimeout("Jina Reader timed out")

        data = body.get("data") or {}
        title = data.get("title") or ""
        markdown = data.get("content") or ""
        published_at = self._parse_published_time(data)

        # Try to extract image from Jina response
        image_url = None
        images = data.get("images")
        if isinstance(images, dict):
            # Jina may return {url: description} mapping
            for img_url in images:
                image_url = img_url
                break
        elif isinstance(images, list) and images:
            image_url = images[0] if isinstance(images[0], str) else None

        # Fallback: scan markdown for inline images if no image found yet
        if not image_url and markdown:
            m = re.search(r'!\[.*?\]\((https?://[^\)]+)\)', markdown)
            if m:
                image_url = m.group(1)

        # If Jina didn't provide published_at, try extracting from markdown
        if published_at is None and markdown:
            published_at = self._extract_datetime_from_text(markdown)

        return {
            "url": url,
            "resolved_url": url,
            "domain": extract_domain(url),
            "title": title,
            "markdown": markdown,
            "html": "",
            "metadata": data,
            "image_url": image_url,
            "published_at": published_at,
            "status": "success",
            "scrape_layer": "jina",
        }

    async def _fallback_scrape(self, url: str, timeout: int | None = None) -> dict[str, Any]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        async with httpx.AsyncClient(timeout=timeout or 20, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        title = extract_domain(url)
        m = _TITLE_RE.search(html)
        if m:
            title = m.group(1).strip()

        # Extract og:image
        image_url = None
        m = _OG_IMAGE_RE.search(html)
        if m:
            image_url = m.group(1).strip()

        # Fallback: score all images, pick the best (prefer content over template)
        if not image_url:
            base_match = re.search(r'(https?://[^/]+)', url)
            base = base_match.group(1) if base_match else ""
            candidates = []  # (score, src)
            for pattern in [
                r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
                r'<img[^>]+src=["\'](//[^"\']+)["\']',
                r'<img[^>]+data-src=["\'](https?://[^"\']+)["\']',
                r'<img[^>]+data-original=["\'](https?://[^"\']+)["\']',
            ]:
                for src in re.findall(pattern, html, re.IGNORECASE):
                    if src.startswith('//'):
                        src = 'https:' + src
                    score = _score_image_src(src, html)
                    if score > -999:
                        candidates.append((score, src))
            if base:
                for src in re.findall(r'<img[^>]+src=["\'](/[^"\']+\.(?:jpg|jpeg|png|gif|webp))["\']', html, re.IGNORECASE):
                    full = base + src
                    score = _score_image_src(full, html)
                    if score > -999:
                        candidates.append((score, full))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                image_url = candidates[0][1]
                logger.debug("Image scoring: best=%s score=%d (total %d candidates)", image_url[:80], candidates[0][0], len(candidates))

        # Extract published time
        published_at = None
        m = _ARTICLE_PUBLISHED_RE.search(html)
        if m:
            published_at = parse_datetime(m.group(1).strip())

        # Targeted HTML-to-markdown conversions before stripping everything
        # 1. Strip noise elements entirely
        md = re.sub(
            r"<(script|style|nav|footer|header|noscript|iframe)[^>]*>.*?</\1>",
            " ", html, flags=re.IGNORECASE | re.DOTALL,
        )
        # 2. Convert headings
        md = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n\n## \1\n\n", md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n\n### \1\n\n", md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n\n#### \1\n\n", md, flags=re.IGNORECASE | re.DOTALL)
        md = re.sub(r"<h4[^>]*>(.*?)</h4>", r"\n\n#### \1\n\n", md, flags=re.IGNORECASE | re.DOTALL)
        # 3. Convert links
        md = re.sub(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            r"[\2](\1)", md, flags=re.IGNORECASE | re.DOTALL,
        )
        # 4. Convert block-level elements to paragraph breaks
        md = re.sub(r"<(?:p|li|div|article|section|main)[^>]*>", r"\n\n", md, flags=re.IGNORECASE)
        md = re.sub(r"</(?:p|li|div|article|section|main)>", r"\n\n", md, flags=re.IGNORECASE)
        # 5. Convert <br> to newline
        md = re.sub(r"<br\s*/?>", "\n", md, flags=re.IGNORECASE)
        # 6. Strip remaining HTML tags
        md = re.sub(r"<[^>]+>", " ", md)
        # 7. Normalize whitespace
        md = re.sub(r"\n\s*\n\s*\n+", "\n\n", md)
        md = re.sub(r" {2,}", " ", md)
        md = md.strip()
        markdown = md[:8000]

        # Fallback: scan for inline markdown images if og:image not found
        if not image_url and markdown:
            m = re.search(r'!\[.*?\]\((https?://[^\)]+)\)', markdown)
            if m:
                image_url = m.group(1)

        if published_at is None:
            published_at = self._extract_datetime_from_text(markdown)

        return {
            "url": url,
            "resolved_url": str(response.url),
            "domain": extract_domain(url),
            "title": title,
            "markdown": markdown,
            "html": html[:16000],
            "metadata": {},
            "image_url": image_url,
            "published_at": published_at,
            "status": "fallback",
            "scrape_layer": "direct_http",
        }

    def _should_skip_direct_http(self, url: str, exc: Exception) -> bool:
        domain = extract_domain(url)
        expires_at = self._skip_direct_http_domains.get(domain, 0.0)
        if expires_at and expires_at > time.time():
            return True
        if expires_at and expires_at <= time.time():
            self._skip_direct_http_domains.pop(domain, None)

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in {403, 451}:
                self._skip_direct_http_domains[domain] = time.time() + self._skip_ttl_seconds
                return True

        text = str(exc).lower()
        if "certificate verify failed" in text or "hostname mismatch" in text or "tls" in text:
            self._skip_direct_http_domains[domain] = time.time() + self._skip_ttl_seconds
            return True
        return False

    @staticmethod
    def _parse_published_time(data: dict[str, Any]):
        for key in ("publishedTime", "datePublished", "date"):
            val = data.get(key)
            if val:
                parsed = parse_datetime(val)
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
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
