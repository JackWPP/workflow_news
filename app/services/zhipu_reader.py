"""智谱 Web Reader API 客户端.

POST https://open.bigmodel.cn/api/paas/v4/reader
input: {url, timeout, return_format, retain_images}
output: reader_result.content (markdown), reader_result.title

V2 Phase C.2: 作为 ScraperClient 的第二层兜底（Trafilatura 之后、Jina 之前）。
exp2 实测：Trafilatura + 智谱 reader 双兜底 = 100% 命中（60/60）。

注意:
- 单 URL 接口（无批量）
- 不返回 publish_date / author（需上游搜索 API 或 Trafilatura 提供）
- 部分 URL 会 500 + code=1234 "请求异常"，需 fallback
- 国内可达性好，不依赖 Jina API key
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_READER_URL = "https://open.bigmodel.cn/api/paas/v4/reader"


class ZhipuReaderClient:
    """智谱 Web Reader API 客户端。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.zhipu_api_key
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def scrape(
        self, url: str, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """抓单个 URL，返回与 JinaReaderClient.scrape() 兼容的格式。

        Returns:
            {status, url, markdown, title, image_url, published_at, links, ...}
        """
        if not self.enabled:
            return {
                "status": "error",
                "url": url,
                "error": "no_zhipu_api_key",
                "markdown": "",
                "title": "",
                "image_url": None,
                "published_at": None,
                "links": [],
            }

        timeout = timeout_seconds or settings.scrape_timeout_seconds
        # 智谱 reader 自身 timeout 字段上限是 20s
        reader_timeout = min(int(timeout), 20)

        payload = {
            "url": url,
            "timeout": reader_timeout,
            "no_cache": False,
            "return_format": "markdown",
            "retain_images": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        self._request_count += 1
        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                resp = await client.post(_READER_URL, json=payload, headers=headers)
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            logger.debug("ZhipuReader: request failed for %s: %s", url[:80], exc)
            return {
                "status": "error",
                "url": url,
                "error": str(exc)[:200],
                "markdown": "",
                "title": "",
                "image_url": None,
                "published_at": None,
                "links": [],
            }

        if resp.status_code != 200:
            self._failure_count += 1
            self._consecutive_failures += 1
            logger.debug(
                "ZhipuReader: HTTP %d for %s: %s",
                resp.status_code, url[:80], resp.text[:200],
            )
            return {
                "status": "error",
                "url": url,
                "error": f"http_{resp.status_code}",
                "markdown": "",
                "title": "",
                "image_url": None,
                "published_at": None,
                "links": [],
            }

        try:
            data = resp.json()
        except Exception as exc:
            self._failure_count += 1
            self._consecutive_failures += 1
            return {
                "status": "error",
                "url": url,
                "error": f"json_parse: {str(exc)[:100]}",
                "markdown": "",
                "title": "",
                "image_url": None,
                "published_at": None,
                "links": [],
            }

        rr = data.get("reader_result") or {}
        content = rr.get("content") or ""
        title = rr.get("title") or ""

        if not content.strip():
            self._failure_count += 1
            self._consecutive_failures += 1
            logger.debug("ZhipuReader: empty content for %s", url[:80])
            return {
                "status": "error",
                "url": url,
                "error": "empty_content",
                "markdown": "",
                "title": "",
                "image_url": None,
                "published_at": None,
                "links": [],
            }

        # 抽首张图（markdown 格式 ![..](url)）
        image_url: str | None = None
        m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", content[:3000])
        if m:
            image_url = m.group(1)

        self._consecutive_failures = 0
        logger.debug(
            "ZhipuReader: success for %s (%d chars)", url[:80], len(content)
        )

        return {
            "status": "ok",
            "url": url,
            "resolved_url": rr.get("url") or url,
            "domain": "",
            "title": title,
            "markdown": content,
            "html": "",
            "metadata": rr.get("metadata") or {},
            "image_url": image_url,
            "published_at": None,  # 智谱 reader 不返回发布时间
            "links": [],
            "scrape_layer": "zhipu_reader",
        }

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "provider": "zhipu_reader",
            "enabled": self.enabled,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "state": "degraded" if self._consecutive_failures >= 3 else "healthy",
        }
