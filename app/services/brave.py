from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import settings
from app.utils import extract_domain, parse_datetime


class CircuitBreaker:
    """简单的熔断器：连续失败超过阈值后进入 open 状态，一段时间后尝试恢复。"""

    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 60.0) -> None:
        self._failures = 0
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed / open / half-open

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if time.time() - self._last_failure_time > self._reset_timeout:
                self._state = "half-open"
                return False
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self._threshold:
            self._state = "open"


class BraveSearchClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.brave_api_key
        self.base_url = (base_url or settings.brave_base_url).rstrip("/")
        self._circuit_breaker = CircuitBreaker()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {}

        if self._circuit_breaker.is_open:
            raise ConnectionError("Brave Search 服务暂时不可用（熔断保护中），请稍后重试")

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.base_url}{path}", params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            self._circuit_breaker.record_success()
            return data
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                self._circuit_breaker.record_failure()
            raise
        except Exception:
            self._circuit_breaker.record_failure()
            raise

    async def search(
        self,
        query: str,
        search_type: str = "web",
        count: int = 10,
        country: str | None = None,
        search_lang: str | None = None,
        goggles: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        endpoint_map = {
            "web": "/res/v1/web/search",
            "news": "/res/v1/news/search",
            "images": "/res/v1/images/search",
        }
        params = {
            "q": query,
            "count": count,
            "country": country or settings.brave_country,
            "search_lang": search_lang or settings.brave_search_lang,
            "freshness": "pd",
        }
        if goggles:
            params["goggles_id"] = goggles

        retry_candidates = [
            dict(params),
            {key: value for key, value in params.items() if key != "freshness"},
            {key: value for key, value in params.items() if key not in {"freshness", "search_lang"}},
            {key: value for key, value in params.items() if key not in {"freshness", "search_lang", "country"}},
        ]
        last_exc: Exception | None = None
        payload: dict[str, Any] | None = None
        for retry_params in retry_candidates:
            try:
                payload = await self._request(endpoint_map[search_type], retry_params)
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code != 422:
                    raise
        if payload is None:
            raise last_exc or RuntimeError("Brave search request failed")
        return self._extract_results(payload, search_type)

    async def search_all(self, query: str, search_lang: str, goggles: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        results = await asyncio.gather(
            self.search(query, "web", count=8, search_lang=search_lang, goggles=goggles),
            self.search(query, "news", count=6, search_lang=search_lang, goggles=goggles),
            self.search(query, "images", count=4, search_lang=search_lang, goggles=goggles),
            return_exceptions=True,
        )

        combined: list[dict[str, Any]] = []
        first_error: Exception | None = None
        for bucket in results:
            if isinstance(bucket, Exception):
                if first_error is None:
                    first_error = bucket
                continue
            combined.extend(bucket)
        if not combined and first_error is not None:
            raise first_error
        return combined

    def _extract_results(self, payload: dict[str, Any], search_type: str) -> list[dict[str, Any]]:
        if not payload:
            return []

        bucket = payload.get(search_type) or payload
        rows = bucket.get("results") or bucket.get("value") or []
        results: list[dict[str, Any]] = []
        for row in rows:
            url = row.get("url") or row.get("profile", {}).get("url") or row.get("thumbnail", {}).get("source")
            title = row.get("title") or row.get("name") or row.get("page_title")
            if not url or not title:
                continue

            snippet = row.get("description") or row.get("snippet") or row.get("summary")
            image_url = row.get("thumbnail", {}).get("src") or row.get("image", {}).get("url")
            published = (
                row.get("page_age")
                or row.get("age")
                or row.get("date")
                or row.get("date_published")
                or row.get("datePublished")
            )
            results.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "image_url": image_url,
                    "published_at": parse_datetime(published),
                    "domain": extract_domain(url),
                    "search_type": search_type,
                    "metadata": row,
                }
            )
        return results
