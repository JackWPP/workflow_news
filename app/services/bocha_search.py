from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.utils import extract_domain

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bochaai.com/v1/web-search"
_AI_SEARCH_URL = "https://api.bochaai.com/v1/ai-search"
_MAX_RETRIES = 3
_RETRY_BACKOFF = [2.0, 5.0, 10.0]


class BochaSearchClient:
    """
    博查 AI Web Search API 客户端。

    返回格式与 ZhipuSearchClient 一致：
      {url, title, snippet, published_at, domain, search_type, result_type, provider, metadata}
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.bocha_api_key
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _post_with_retry(
        self, url: str, payload: dict, headers: dict, query_label: str
    ) -> dict[str, Any] | None:
        for attempt in range(_MAX_RETRIES):
            self._request_count += 1
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    wait += random.uniform(0.0, 0.5)
                    logger.warning(
                        "BochaSearch 429 for '%s', retrying in %.1fs (attempt %d/%d)",
                        query_label, wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                self._failure_count += 1
                self._consecutive_failures += 1
                self._last_error = f"http_{resp.status_code}"
                logger.warning(
                    "BochaSearch returned %d for '%s': %s",
                    resp.status_code, query_label, resp.text[:300],
                )
                return None
        return None

    async def search(
        self,
        query: str,
        count: int | None = None,
        freshness: str = "oneWeek",
        summary: bool = True,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        count = count or settings.bocha_search_count

        payload: dict[str, Any] = {
            "query": query,
            "freshness": freshness,
            "summary": summary,
            "count": count,
        }
        if include_domains:
            payload["include"] = "|".join(include_domains)
        if exclude_domains:
            payload["exclude"] = "|".join(exclude_domains)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            data = await self._post_with_retry(_BASE_URL, payload, headers, query)
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = str(exc)[:200]
            logger.warning("BochaSearch request failed for '%s': %s", query, exc)
            return []
        if data is None:
            return []

        # 响应格式：{code: 200, data: {_type, webPages: {value: [...]}}}
        inner = data.get("data") or data
        web_pages = (inner.get("webPages") or {}).get("value") or []
        if not web_pages:
            self._consecutive_failures = 0
            logger.info("BochaSearch '%s' → 0 results", query)
            return []

        self._consecutive_failures = 0
        self._last_error = ""
        results: list[dict[str, Any]] = []
        for item in web_pages:
            url = item.get("url") or ""
            if not url:
                continue
            domain = extract_domain(url) or item.get("siteName") or ""
            published_at = _parse_date(item.get("datePublished"))
            snippet = item.get("snippet") or ""
            ai_summary = item.get("summary") or ""
            image_url = item.get("thumbnailUrl") or None
            results.append({
                "url": url,
                "title": item.get("name") or "",
                "snippet": ai_summary if ai_summary else snippet,
                "image_url": image_url,
                "published_at": published_at,
                "domain": domain,
                "search_type": "news",
                "result_type": "news",
                "provider": "bocha",
                "metadata": item,
            })

        logger.info("BochaSearch '%s' → %d results", query, len(results))
        return results

    async def ai_search(
        self,
        query: str,
        count: int | None = None,
        freshness: str = "oneWeek",
        stream: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """AI-powered search with summaries and follow-up questions.

        Calls POST https://api.bochaai.com/v1/ai-search and returns results
        in the same format as search(). Also captures answer (AI summary)
        and question (suggested follow-ups) as metadata on the first result.
        """
        if not self.enabled:
            return []

        count = count or settings.bocha_search_count

        payload: dict[str, Any] = {
            "query": query,
            "freshness": freshness,
            "summary": True,
            "count": count,
            "stream": stream,
        }
        if include_domains:
            payload["include"] = "|".join(include_domains)
        if exclude_domains:
            payload["exclude"] = "|".join(exclude_domains)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            data = await self._post_with_retry(_AI_SEARCH_URL, payload, headers, query)
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = str(exc)[:200]
            logger.warning("Bocha ai_search request failed for '%s': %s", query, exc)
            return []
        if data is None:
            return []

        import json as _json

        inner = data.get("data") or data
        # AI Search returns {answer, question, messages[]} (NOT webPages)
        ai_answer = inner.get("answer") or ""
        followup_questions = inner.get("question") or []
        messages = inner.get("messages") or []

        results: list[dict[str, Any]] = []
        for message in messages:
            content_type = message.get("content_type", "")
            if content_type == "webpage":
                content_str = message.get("content", "{}")
                try:
                    content = _json.loads(content_str) if isinstance(content_str, str) else content_str
                except (TypeError, ValueError):
                    logger.warning("Bocha ai_search: failed to parse webpage content")
                    continue
                for item in content.get("value", []):
                    url = item.get("url") or ""
                    if not url:
                        continue
                    domain = extract_domain(url) or item.get("siteName") or ""
                    published_at = _parse_date(item.get("datePublished"))
                    snippet = item.get("snippet") or ""
                    ai_summary = item.get("summary") or ""
                    image_url = item.get("thumbnailUrl") or None
                    results.append({
                        "url": url,
                        "title": item.get("name") or "",
                        "snippet": ai_summary if ai_summary else snippet,
                        "image_url": image_url,
                        "published_at": published_at,
                        "domain": domain,
                        "search_type": "ai_search",
                        "result_type": "news",
                        "provider": "bocha",
                        "metadata": item,
                    })

        if results and (ai_answer or followup_questions):
            results[0]["ai_answer"] = ai_answer
            results[0]["followup_questions"] = followup_questions

        if not results:
            self._consecutive_failures = 0
            logger.info("Bocha ai_search '%s' -> 0 results", query)
            return []

        self._consecutive_failures = 0
        self._last_error = ""

        logger.info("Bocha ai_search '%s' -> %d results", query, len(results))
        return results

    def health_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            health_state = "disabled"
        elif self._consecutive_failures >= 2:
            health_state = "network_failed"
        else:
            health_state = "healthy"
        return {
            "provider": "bocha",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "state": "degraded" if self._consecutive_failures >= 2 else "healthy",
            "health_state": health_state,
        }


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None
