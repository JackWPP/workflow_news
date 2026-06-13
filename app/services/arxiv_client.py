from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ARXIV_API_URL = "http://export.arxiv.org/api/query"
_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

_search_semaphore = asyncio.Semaphore(2)


class ArxivApiClient:
    def __init__(self, max_results: int = 20, timeout: float = 30.0):
        self.max_results = max_results
        self.timeout = timeout

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        sort_by: str = "submittedDate",
        sort_order: str = "descending",
    ) -> list[dict[str, Any]]:
        async with _search_semaphore:
            return await self._search_inner(
                query,
                max_results=max_results or self.max_results,
                sort_by=sort_by,
                sort_order=sort_order,
            )

    async def _search_inner(
        self,
        query: str,
        max_results: int,
        sort_by: str,
        sort_order: str,
    ) -> list[dict[str, Any]]:
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(_ARXIV_API_URL, params=params)
                if resp.status_code != 200:
                    logger.warning("arXiv API returned %d for '%s': %s", resp.status_code, query, resp.text[:300])
                    return []
                return self._parse_response(resp.text, query)
        except Exception as exc:
            logger.warning("arXiv API error for '%s': %s", query, exc)
            return []

    def _parse_response(self, xml_text: str, search_query: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse arXiv XML: %s", exc)
            return []

        results = []
        for entry in root.findall("atom:entry", _ARXIV_NS):
            try:
                result = self._parse_entry(entry, search_query)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug("Failed to parse arXiv entry: %s", exc)
                continue

        return results

    def _parse_entry(self, entry: ET.Element, search_query: str) -> dict[str, Any] | None:
        title_el = entry.find("atom:title", _ARXIV_NS)
        summary_el = entry.find("atom:summary", _ARXIV_NS)
        published_el = entry.find("atom:published", _ARXIV_NS)
        updated_el = entry.find("atom:updated", _ARXIV_NS)

        if title_el is None:
            return None

        title = (title_el.text or "").strip().replace("\n", " ")
        summary = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""

        link = ""
        for link_el in entry.findall("atom:link", _ARXIV_NS):
            if link_el.get("type") == "text/html":
                link = link_el.get("href", "")
                break
        if not link:
            id_el = entry.find("atom:id", _ARXIV_NS)
            if id_el is not None and id_el.text:
                link = id_el.text.strip()

        if not link:
            return None

        published_at = None
        date_el = published_el or updated_el
        if date_el is not None and date_el.text:
            try:
                published_at = datetime.fromisoformat(date_el.text.replace("Z", "+00:00"))
            except ValueError:
                pass

        categories = []
        for cat_el in entry.findall("atom:category", _ARXIV_NS):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)

        authors = []
        for author_el in entry.findall("atom:author", _ARXIV_NS):
            name_el = author_el.find("atom:name", _ARXIV_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        arxiv_id = ""
        id_el = entry.find("atom:id", _ARXIV_NS)
        if id_el is not None and id_el.text:
            arxiv_id = id_el.text.strip().split("/")[-1]

        return {
            "url": link,
            "title": title,
            "snippet": summary[:500] if summary else "",
            "published_at": published_at,
            "domain": "arxiv.org",
            "search_type": "arxiv_api",
            "result_type": "academic",
            "provider": "arxiv",
            "metadata": {
                "search_query": search_query,
                "arxiv_id": arxiv_id,
                "categories": categories,
                "authors": authors[:5],
                "primary_category": categories[0] if categories else "",
            },
        }
