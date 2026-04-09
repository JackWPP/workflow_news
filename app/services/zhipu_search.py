"""
zhipu_search.py — 智谱 AI Web Search API 客户端

使用 search_pro 引擎（多引擎协作：搜狗/夸克/自研），专为中文搜索优化。
REST 端点：POST https://open.bigmodel.cn/api/paas/v4/web_search
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.utils import extract_domain

logger = logging.getLogger(__name__)

_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"


class ZhipuSearchClient:
    """
    智谱 AI Web Search API 客户端。

    返回的每条结果格式与 BraveSearchClient 一致：
      {url, title, snippet, published_at, domain, search_type, metadata}
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.zhipu_api_key
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        count: int | None = None,
        recency: str = "oneWeek",
    ) -> list[dict[str, Any]]:
        """
        搜索并返回统一格式的结果列表。

        参数:
            query: 搜索词���最长 70 字符，由 Agent 自主构造）
            count: 返回条数（1-50），默认读取 settings.zhipu_search_count
            recency: 时间范围过滤，oneDay/oneWeek/oneMonth/oneYear/noLimit
        """
        if not self.enabled:
            return []

        count = count or settings.zhipu_search_count
        # 智谱限制 search_query 最长 70 字符
        query = query[:70]

        payload: dict[str, Any] = {
            "search_engine": settings.zhipu_search_engine,
            "search_query": query,
            "search_intent": False,
            "count": count,
            "search_recency_filter": recency,
            "content_size": "high",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            self._request_count += 1
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_BASE_URL, json=payload, headers=headers)
                if resp.status_code != 200:
                    self._failure_count += 1
                    self._consecutive_failures += 1
                    self._last_error = f"http_{resp.status_code}"
                    logger.warning(
                        "ZhipuSearch returned %d for '%s': %s",
                        resp.status_code, query, resp.text[:300],
                    )
                    return []
                data = resp.json()
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            self._last_error = str(exc)[:200]
            logger.warning("ZhipuSearch request failed for '%s': %s", query, exc)
            return []

        raw_results: list[dict[str, Any]] = data.get("search_result") or []
        if not raw_results:
            self._consecutive_failures = 0
            logger.info("ZhipuSearch '%s' → 0 results (empty search_result)", query)
            return []

        self._consecutive_failures = 0
        self._last_error = ""
        results: list[dict[str, Any]] = []
        for item in raw_results:
            url = item.get("link") or ""
            if not url:
                continue
            domain = extract_domain(url) or item.get("media") or ""
            published_at = _parse_date(item.get("publish_date"))
            results.append({
                "url": url,
                "title": item.get("title") or "",
                "snippet": item.get("content") or "",
                "image_url": None,
                "published_at": published_at,
                "domain": domain,
                "search_type": "news",
                "result_type": "news",
                "provider": "zhipu",
                "metadata": item,
            })

        logger.info("ZhipuSearch '%s' → %d results", query, len(results))
        return results

    def health_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            health_state = "disabled"
        elif self._consecutive_failures >= 2:
            health_state = "network_failed"
        else:
            health_state = "healthy"
        return {
            "provider": "zhipu",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "state": "degraded" if self._consecutive_failures >= 2 else "healthy",
            "health_state": health_state,
        }


def _parse_date(value: str | None) -> datetime | None:
    """解析智谱返回的 publish_date 字符串（格式：'2025-04-08'）为带时区的 datetime。"""
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
