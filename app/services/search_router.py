"""
search_router.py — 统一搜索路由器

替代各处直连 Bocha/Zhipu 的分散调用。
提供统一的 search() 接口，内部按 provider 健康度自动 failover。
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.utils import extract_domain

logger = logging.getLogger(__name__)


# ── 唯一 blocklist（其他地方的副本应逐步删除）──
_BLOCKED_DOMAINS: set[str] = {
    # PR / 营销
    "openpr.com", "prnewswire.com", "prnasia.com", "businesswire.com",
    "globenewswire.com", "coherentmarketinsights.com", "gminsights.com",
    "grandviewresearch.com",
    # 百科 / 社区
    "baike.baidu.com", "zhuanlan.zhihu.com", "bilibili.com",
    # 财经 / 投资
    "cn.investing.com", "investing.com",
    # B2B 电商
    "made-in-china.com", "alibaba.com", "1688.com", "globalsources.com",
    "indiamart.com", "b2b168.com", "jdzj.com", "hbsztv.com",
    "stockstar.com", "eastmoney.com", "10jqka.com.cn",
    "china-packcon.com", "china-ipif.com",
    # 台湾媒体
    "digitimes.com.tw", "udn.com", "ltn.com.tw", "chinatimes.com",
    "yahoo.com.tw", "tw.news.yahoo.com", "ctee.com.tw",
    "money.udn.com", "technews.tw", "bnext.com.tw", "ettoday.net",
    "setn.com", "storm.mg", "cna.com.tw", "taiwannews.com.tw",
}


def _is_blocked(domain: str) -> bool:
    if domain in _BLOCKED_DOMAINS:
        return True
    parts = domain.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in _BLOCKED_DOMAINS:
            return True
    return False


def _query_hash(query: str) -> str:
    return hashlib.md5(query.encode()).hexdigest()


def _cache_part(values: list[str] | None) -> str:
    if not values:
        return "-"
    return "|".join(sorted(str(value).strip().lower() for value in values if value))


class SearchRouter:
    """统一搜索路由器。按 provider 健康度自动 failover。"""

    def __init__(
        self,
        bocha_client: Any = None,
        zhipu_client: Any = None,
    ) -> None:
        self._bocha = bocha_client
        self._zhipu = zhipu_client
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_ttl = 86400.0  # 24h

    async def search(
        self,
        query: str,
        language: str = "zh",
        max_results: int = 10,
        freshness: str = "oneWeek",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """统一搜索入口。自动 failover + 缓存 + blocklist 过滤。"""

        # 检查缓存
        cache_key = "_".join(
            [
                _query_hash(query),
                language,
                str(max_results),
                freshness,
                _cache_part(include_domains),
                _cache_part(exclude_domains),
            ]
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl:
            logger.debug("SearchRouter: cache hit for '%s'", query[:50])
            return cached[1]

        results: list[dict[str, Any]] = []

        # 尝试 Bocha
        if self._bocha and self._bocha.enabled:
            try:
                bocha_results = await self._bocha.search(
                    query, count=max_results, freshness=freshness,
                    include_domains=include_domains, exclude_domains=exclude_domains,
                )
                if bocha_results:
                    results = bocha_results
                    logger.info("SearchRouter: bocha returned %d for '%s'", len(results), query[:50])
            except Exception as exc:
                logger.warning("SearchRouter: bocha failed for '%s': %s", query[:50], exc)

        # Bocha 空结果 → 尝试 Zhipu
        if not results and self._zhipu and self._zhipu.enabled:
            try:
                zhipu_results = await self._zhipu.search(query, count=max_results, recency=freshness)
                if zhipu_results:
                    results = zhipu_results
                    logger.info("SearchRouter: zhipu fallback returned %d for '%s'", len(results), query[:50])
            except Exception as exc:
                logger.warning("SearchRouter: zhipu failed for '%s': %s", query[:50], exc)

        # blocklist 过滤
        filtered = []
        for r in results:
            domain = r.get("domain") or extract_domain(r.get("url", ""))
            if _is_blocked(domain):
                continue
            filtered.append(r)

        # 缓存
        self._cache[cache_key] = (time.time(), filtered)

        return filtered[:max_results]

    async def ai_search(
        self,
        query: str,
        count: int = 10,
        freshness: str = "oneWeek",
    ) -> list[dict[str, Any]]:
        """AI 搜索（目前只走 Bocha ai_search）。"""
        if not self._bocha or not self._bocha.enabled:
            return []
        try:
            return await self._bocha.ai_search(query, count=count, freshness=freshness)
        except Exception as exc:
            logger.warning("SearchRouter: ai_search failed for '%s': %s", query[:50], exc)
            return []

    def health_snapshot(self) -> dict[str, Any]:
        """返回各 provider 健康状态。"""
        result = {}
        if self._bocha:
            result["bocha"] = self._bocha.health_snapshot()
        if self._zhipu:
            result["zhipu"] = self._zhipu.health_snapshot()
        return result

    def clear_cache(self) -> None:
        """清空搜索缓存。"""
        self._cache.clear()
