"""
zhipu_search.py — 智谱 AI Web Search API 客户端

DEPRECATED: 当前系统已统一使用 BochaSearchClient。
本文件保留供未来可能的重新启用，不再被主流程引用。

使用 search_pro 引擎（多引擎协作：搜狗/夸克/自研），专为中文搜索优化。
REST 端点：POST https://open.bigmodel.cn/api/paas/v4/web_search
"""
from __future__ import annotations

import logging
import time
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

    # V2 Phase A.5: 仅 search_pro_sogou / search_pro_quark 在中文 query 上 link 正常
    _VALID_ZHIPU_ENGINES = {"search_pro_sogou", "search_pro_quark"}
    _engine_warned = False

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.zhipu_api_key
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0
        self._last_error = ""
        # ── V2 Phase 0.5: 累积统计（用于 health_snapshot 暴露）──
        self._total_results = 0          # 累计返回的结果总数
        self._latencies_ms: list[int] = []  # 每次成功请求的延迟（ms）
        self._content_chars: list[int] = []  # 每条结果 content 的字数（采样）
        # V2 Phase A.5: 防御性校验 —— search_std/search_pro 在中文 query 上 link 100% 损坏
        if not ZhipuSearchClient._engine_warned and settings.zhipu_search_engine not in ZhipuSearchClient._VALID_ZHIPU_ENGINES:
            ZhipuSearchClient._engine_warned = True
            logger.warning(
                "ZHIPU_SEARCH_ENGINE='%s' may have empty link field on Chinese queries. "
                "Recommended: search_pro_sogou. See experiments/search_v2/reports/exp4_report.md",
                settings.zhipu_search_engine,
            )

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
            _start_time = time.perf_counter()
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
            content = item.get("content") or ""
            results.append({
                "url": url,
                "title": item.get("title") or "",
                "snippet": content,               # 维持现状: 完整 content（下游 _row_is_relevant 用它做关键词匹配）
                "summary_full": content,          # V2 Phase A.4: 显式 = content（与 snippet 同义）
                "snippet_raw": content[:200],     # V2 Phase A.4: 短 snippet 用于快速过滤
                "image_url": item.get("icon") or None,
                "published_at": published_at,
                "domain": domain,
                "search_type": "news",
                "result_type": "news",
                "provider": "zhipu",
                "metadata": item,
            })
            # V2 Phase 0.5: 累积 content 字数样本（限制存量避免内存膨胀）
            if content and len(self._content_chars) < 1000:
                self._content_chars.append(len(content))

        # V2 Phase 0.5: 累积统计
        latency_ms = int((time.perf_counter() - _start_time) * 1000)
        if len(self._latencies_ms) < 1000:
            self._latencies_ms.append(latency_ms)
        self._total_results += len(results)

        logger.info("ZhipuSearch '%s' → %d results", query, len(results))
        return results

    def health_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            health_state = "disabled"
        elif self._consecutive_failures >= 2:
            health_state = "network_failed"
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
            "provider": "zhipu",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "state": "degraded" if self._consecutive_failures >= 2 else "healthy",
            "health_state": health_state,
            # ── V2 Phase 0.5 新增字段 ──
            "avg_results_per_query": avg_results,
            "p50_content_chars": _pct(self._content_chars, 0.5),
            "p95_content_chars": _pct(self._content_chars, 0.95),
            "p50_latency_ms": _pct(self._latencies_ms, 0.5),
            "p95_latency_ms": _pct(self._latencies_ms, 0.95),
            "total_results": self._total_results,
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
