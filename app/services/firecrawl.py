from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import settings
from app.services.brave import CircuitBreaker
from app.utils import extract_domain, parse_datetime


class FirecrawlClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.firecrawl_api_key
        self.base_url = (base_url or settings.firecrawl_base_url).rstrip("/")
        self._circuit_breaker = CircuitBreaker()
        self._request_count = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _request(self, path: str, payload: dict[str, Any], timeout_seconds: int | None = None) -> dict[str, Any]:
        if self._circuit_breaker.is_open:
            raise ConnectionError("Firecrawl 服务暂时不可用（熔断保护中），请稍后重试")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            self._request_count += 1
            async with httpx.AsyncClient(timeout=timeout_seconds or settings.scrape_timeout_seconds) as client:
                response = await client.post(f"{self.base_url}{path}", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            self._circuit_breaker.record_success()
            self._last_error = ""
            return data
        except Exception as exc:
            self._last_error = str(exc)[:200]
            self._circuit_breaker.record_failure()
            raise

    async def scrape(self, url: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        if not self.enabled:
            return await self._fallback_scrape(url)

        payload = {
            "url": url,
            "formats": ["markdown", "html"],
            "onlyMainContent": True,
            "location": {
                "country": settings.firecrawl_country,
                "languages": ["zh-CN", "en-US"],
            },
        }
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                data = await self._request("/scrape", payload, timeout_seconds=timeout_seconds)
                break
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
        else:
            raise last_exc or httpx.ReadTimeout("Firecrawl scrape timed out")

        page = data.get("data") or {}
        title = page.get("metadata", {}).get("title") or page.get("title")
        markdown = page.get("markdown")
        html = page.get("html")
        metadata = page.get("metadata", {}) or {}
        return {
            "url": url,
            "resolved_url": metadata.get("sourceURL") or metadata.get("url") or url,
            "domain": extract_domain(url),
            "title": title,
            "markdown": markdown,
            "html": html,
            "metadata": metadata,
            "image_url": metadata.get("ogImage"),
            "published_at": self._extract_published_at(metadata, title, markdown, html),
            "status": "success",
            "scrape_layer": "firecrawl",
        }

    async def map(self, url: str) -> list[str]:
        if not self.enabled:
            return []

        payload = {"url": url}
        data = await self._request("/map", payload)
        return data.get("links") or data.get("data") or []

    async def search(
        self,
        query: str,
        limit: int = 5,
        country: str | None = None,
        timeout: int = 60000,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        payload = {
            "query": query,
            "limit": limit,
            "sources": ["web", "news"],
            "country": country or settings.firecrawl_country,
            "timeout": timeout,
        }
        data = await self._request("/search", payload, timeout_seconds=max(30, timeout // 1000 + 5))
        bucket = data.get("data") or {}
        results: list[dict[str, Any]] = []

        for row in bucket.get("web") or []:
            url = row.get("url") or (row.get("metadata") or {}).get("sourceURL") or (row.get("metadata") or {}).get("url")
            title = row.get("title") or (row.get("metadata") or {}).get("title")
            if not url or not title:
                continue
            results.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": row.get("description") or (row.get("metadata") or {}).get("description"),
                    "image_url": None,
                    "published_at": parse_datetime(
                        row.get("date")
                        or (row.get("metadata") or {}).get("publishedTime")
                        or (row.get("metadata") or {}).get("article:published_time")
                    ),
                    "domain": extract_domain(url),
                    "search_type": "web",
                    "result_type": "web",
                    "provider": "firecrawl",
                    "metadata": {
                        **row,
                        "provider": "firecrawl_search",
                    },
                }
            )

        for row in bucket.get("news") or []:
            url = row.get("url") or (row.get("metadata") or {}).get("sourceURL") or (row.get("metadata") or {}).get("url")
            title = row.get("title") or (row.get("metadata") or {}).get("title")
            if not url or not title:
                continue
            results.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": row.get("snippet") or row.get("description") or (row.get("metadata") or {}).get("description"),
                    "image_url": row.get("imageUrl"),
                    "published_at": parse_datetime(
                        row.get("date")
                        or (row.get("metadata") or {}).get("publishedTime")
                        or (row.get("metadata") or {}).get("article:published_time")
                    ),
                    "domain": extract_domain(url),
                    "search_type": "news",
                    "result_type": "news",
                    "provider": "firecrawl",
                    "metadata": {
                        **row,
                        "provider": "firecrawl_search",
                    },
                }
            )

        return results

    def health_snapshot(self) -> dict[str, Any]:
        circuit_state = self._circuit_breaker.snapshot().get("state")
        if not self.enabled:
            health_state = "disabled"
        elif circuit_state == "open":
            health_state = "circuit_open"
        elif self._last_error:
            health_state = "network_failed"
        else:
            health_state = "healthy"
        return {
            **self._circuit_breaker.snapshot(),
            "provider": "firecrawl",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "last_error": self._last_error,
            "health_state": health_state,
        }

    async def _fallback_scrape(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        title_match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else extract_domain(url)
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        markdown = text[:8000]

        return {
            "url": url,
            "resolved_url": str(response.url),
            "domain": extract_domain(url),
            "title": title,
            "markdown": markdown,
            "html": html[:16000],
            "metadata": {},
            "image_url": None,
            "published_at": None,
            "status": "fallback",
            "scrape_layer": "direct_http",
        }

    def _extract_published_at(
        self,
        metadata: dict[str, Any],
        title: str | None,
        markdown: str | None,
        html: str | None,
    ):
        for candidate in [
            metadata.get("publishedTime"),
            metadata.get("article:published_time"),
            metadata.get("article:modified_time"),
            metadata.get("pubdate"),
            metadata.get("date"),
            metadata.get("publishdate"),
        ]:
            parsed = parse_datetime(candidate)
            if parsed is not None:
                return parsed

        scoped_texts = []
        for text in [html or "", markdown or ""]:
            scoped = self._title_window(text, title)
            if scoped:
                scoped_texts.append(scoped)
        scoped_texts.extend([html or "", markdown or ""])

        for text in scoped_texts:
            parsed = self._extract_datetime_from_text(text)
            if parsed is not None:
                return parsed
        return None

    def _title_window(self, text: str, title: str | None, radius: int = 1800) -> str:
        if not text or not title:
            return ""
        needles = [title]
        if " - " in title:
            needles.append(title.split(" - ", 1)[0].strip())
        for needle in needles:
            if not needle:
                continue
            idx = text.find(needle)
            if idx >= 0:
                return text[max(0, idx - radius): idx + radius]
        return ""

    def _extract_datetime_from_text(self, text: str):
        if not text:
            return None
        patterns = [
            r"(?:日期|发布时间|發佈時間|發佈日期|发布于)\s*[：:]\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            r'datetime=["\'](20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\+\d{2}:\d{2}|Z)?)["\']',
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
