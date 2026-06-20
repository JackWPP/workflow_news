from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.utils import extract_domain

logger = logging.getLogger(__name__)

_search_semaphore = asyncio.Semaphore(2)

_BASE_URL = "https://api.bochaai.com/v1/web-search"
_AI_SEARCH_URL = "https://api.bochaai.com/v1/ai-search"


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
        self._consecutive_empty_queries = 0
        self._last_api_code: int | None = None
        # ── V2 Phase 0.5: 累积统计（用于 health_snapshot 暴露）──
        self._total_results = 0          # 累计返回的结果总数
        self._latencies_ms: list[int] = []  # 每次成功请求的延迟（ms）
        self._summary_chars: list[int] = []  # 每条结果 summary 的字数（采样）

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        count: int | None = None,
        freshness: str = "oneWeek",
        summary: bool = True,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        async with _search_semaphore:
            return await self._search_inner(
                query, count=count, freshness=freshness, summary=summary,
                include_domains=include_domains, exclude_domains=exclude_domains,
            )

    async def _search_inner(
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
            self._request_count += 1
            _start_time = time.perf_counter()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_BASE_URL, json=payload, headers=headers)
                if resp.status_code != 200:
                    self._failure_count += 1
                    self._consecutive_failures += 1
                    self._last_error = f"http_{resp.status_code}"
                    logger.warning(
                        "BochaSearch returned %d for '%s': %s",
                        resp.status_code, query, resp.text[:300],
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = str(exc)[:200]
            logger.warning("BochaSearch request failed for '%s': %s", query, exc)
            return []

        api_code = data.get("code")
        if api_code and api_code != 200:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = f"api_code_{api_code}"
            self._last_api_code = api_code
            logger.warning("BochaSearch API error code %d for '%s': %s", api_code, query[:80], data.get("msg", ""))
            return []

        # 响应格式：{code: 200, data: {_type, webPages: {value: [...]}}}
        inner = data.get("data") or data
        web_pages = (inner.get("webPages") or {}).get("value") or []
        if not web_pages:
            self._consecutive_empty_queries += 1
            logger.info("BochaSearch '%s' → 0 results (consecutive_empty=%d)", query, self._consecutive_empty_queries)
            return []

        self._consecutive_failures = 0
        self._consecutive_empty_queries = 0
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
                "snippet": ai_summary if ai_summary else snippet,  # 维持现状（下游已依赖）
                "summary_full": ai_summary,                          # V2 Phase A.2: 显式 800字 summary
                "snippet_raw": snippet,                              # V2 Phase A.2: 原始 100字 snippet
                "image_url": image_url,
                "published_at": published_at,
                "domain": domain,
                "search_type": "news",
                "result_type": "news",
                "provider": "bocha",
                "metadata": item,
            })
            # V2 Phase 0.5: 累积 summary 字数样本（限制存量避免内存膨胀）
            if ai_summary and len(self._summary_chars) < 1000:
                self._summary_chars.append(len(ai_summary))

        # V2 Phase 0.5: 累积统计
        latency_ms = int((time.perf_counter() - _start_time) * 1000)
        if len(self._latencies_ms) < 1000:
            self._latencies_ms.append(latency_ms)
        self._total_results += len(results)

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
            self._request_count += 1
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_AI_SEARCH_URL, json=payload, headers=headers)
                if resp.status_code != 200:
                    self._failure_count += 1
                    self._consecutive_failures += 1
                    self._last_error = f"ai_search_http_{resp.status_code}"
                    logger.warning(
                        "Bocha ai_search returned %d for '%s': %s",
                        resp.status_code, query, resp.text[:300],
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = str(exc)[:200]
            logger.warning("Bocha ai_search request failed for '%s': %s", query, exc)
            return []

        api_code = data.get("code")
        if api_code and api_code != 200:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = f"ai_search_api_code_{api_code}"
            self._last_api_code = api_code
            logger.warning("BochaSearch ai_search API error code %d for '%s': %s", api_code, query[:80], data.get("msg", ""))
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
                        "snippet": ai_summary if ai_summary else snippet,  # 维持现状（下游已依赖）
                        "summary_full": ai_summary,                          # V2 Phase A.2: 显式 800字 summary
                        "snippet_raw": snippet,                              # V2 Phase A.2: 原始 100字 snippet
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
            self._consecutive_empty_queries += 1
            logger.info("Bocha ai_search '%s' -> 0 results (consecutive_empty=%d)", query, self._consecutive_empty_queries)
            return []

        self._consecutive_failures = 0
        self._consecutive_empty_queries = 0
        self._last_error = ""

        logger.info("Bocha ai_search '%s' -> %d results", query, len(results))
        return results

    def health_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            health_state = "disabled"
        elif self._consecutive_failures >= 2:
            health_state = "network_failed"
        elif self._consecutive_empty_queries >= 5:
            health_state = "degraded"
        else:
            health_state = "healthy"

        # V2 Phase 0.5: 累积统计衍生指标
        def _pct(lst: list[int], p: float) -> int:
            if not lst:
                return 0
            s = sorted(lst)
            return s[min(len(s) - 1, int(len(s) * p))]

        successful_requests = max(self._request_count - self._failure_count, 0)
        avg_results = (
            round(self._total_results / successful_requests, 1)
            if successful_requests > 0 else 0
        )

        return {
            "provider": "bocha",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_empty_queries": self._consecutive_empty_queries,
            "last_api_code": self._last_api_code,
            "last_error": self._last_error,
            "state": "degraded" if self._consecutive_failures >= 2 else "healthy",
            "health_state": health_state,
            # ── V2 Phase 0.5 新增字段 ──
            "avg_results_per_query": avg_results,
            "p50_summary_chars": _pct(self._summary_chars, 0.5),
            "p95_summary_chars": _pct(self._summary_chars, 0.95),
            "p50_latency_ms": _pct(self._latencies_ms, 0.5),
            "p95_latency_ms": _pct(self._latencies_ms, 0.95),
            "total_results": self._total_results,
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
