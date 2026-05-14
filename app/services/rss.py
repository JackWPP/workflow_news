from __future__ import annotations

from typing import Any

import feedparser
import httpx

from app.utils import extract_domain, parse_datetime


async def fetch_feed_entries(feed_url: str, source_name: str, source_type: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(feed_url, follow_redirects=True)
        response.raise_for_status()

    parsed = feedparser.parse(response.text)
    entries: list[dict[str, Any]] = []
    for entry in parsed.entries[:12]:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        # content:encoded has the full body; summary (description) is truncated
        content_encoded = ""
        content_list = entry.get("content", [])
        if content_list and isinstance(content_list, list):
            content_encoded = content_list[0].get("value", "")
        entries.append(
            {
                "url": link,
                "title": title,
                "snippet": entry.get("summary"),
                "content_encoded": content_encoded,
                "image_url": None,
                "published_at": parse_datetime(entry.get("published") or entry.get("updated")),
                "domain": extract_domain(link),
                "search_type": "rss",
                "metadata": {"feed_url": feed_url, "source_name": source_name},
                "source_name": source_name,
                "source_type": source_type,
            }
        )
    return entries
