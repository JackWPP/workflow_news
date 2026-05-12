from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.harness import DEFAULT_BLOCKED_DOMAINS
from app.utils import extract_domain

logger = logging.getLogger(__name__)

_BLOCKED_SET: set[str] = set(DEFAULT_BLOCKED_DOMAINS)


def _is_blocked(domain: str) -> bool:
    if domain in _BLOCKED_SET:
        return True
    parts = domain.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in _BLOCKED_SET:
            return True
    return False


class SearchEngine:

    def __init__(self, bocha_client: Any = None, zhipu_client: Any = None) -> None:
        self._bocha = bocha_client
        self._zhipu = zhipu_client  # deprecated, kept for compatibility

    async def search(
        self,
        query: str,
        language: str = "zh",
        max_results: int = 10,
        timeout: float = 30.0,
        source_order: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if source_order is None:
            source_order = ["bocha"]

        seen_urls: set[str] = set()
        results: list[dict[str, Any]] = []

        for source in source_order:
            if len(results) >= max_results:
                break
            try:
                batch = await asyncio.wait_for(
                    self._search_source(source, query, language),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "SearchEngine: %s timed out for '%s'", source, query[:80]
                )
                continue
            except Exception:
                logger.warning(
                    "SearchEngine: %s failed for '%s'",
                    source,
                    query[:80],
                    exc_info=True,
                )
                continue

            for item in batch:
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                domain = item.get("domain") or extract_domain(url)
                if _is_blocked(domain):
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "url": url,
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "domain": domain,
                        "published_at": item.get("published_at"),
                        "image_url": item.get("image_url"),
                    }
                )
                if len(results) >= max_results:
                    break

        return results

    async def _search_source(
        self, source: str, query: str, language: str
    ) -> list[dict[str, Any]]:
        if source == "bocha":
            return await self._search_bocha(query)
        if source == "zhipu":
            return await self._search_zhipu(query)
        logger.warning("SearchEngine: unknown source '%s'", source)
        return []

    async def _search_bocha(self, query: str) -> list[dict[str, Any]]:
        if not self._bocha or not self._bocha.enabled:
            return []
        return await self._bocha.search(query)

    async def _search_zhipu(self, query: str) -> list[dict[str, Any]]:
        if not self._zhipu or not self._zhipu.enabled:
            return []
        return await self._zhipu.search(query)

    async def batch_search(
        self,
        queries: list[str],
        language: str = "zh",
        max_results: int = 10,
        concurrency: int = 5,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(concurrency)

        async def _search_one(query: str) -> list[dict[str, Any]]:
            async with semaphore:
                return await self.search(
                    query, language=language, max_results=max_results
                )

        tasks = [_search_one(q) for q in queries]
        all_batches = await asyncio.gather(*tasks)

        seen_urls: set[str] = set()
        merged: list[dict[str, Any]] = []
        for batch in all_batches:
            for item in batch:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(item)

        return merged
