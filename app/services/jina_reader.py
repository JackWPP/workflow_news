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

        # Extract published time
        published_at = None
        m = _ARTICLE_PUBLISHED_RE.search(html)
        if m:
            published_at = parse_datetime(m.group(1).strip())

        # Strip HTML to plain text
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        markdown = text[:8000]

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
