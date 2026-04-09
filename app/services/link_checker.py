"""
link_checker.py — 链接可用性检查

在 Phase 2 (文章处理) 和 Phase 3 (综合) 之间运行，
确保最终给用户的链接都是可访问的。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.utils import normalize_external_url

logger = logging.getLogger(__name__)


@dataclass
class LinkCheckResult:
    """单个链接的检查结果。"""
    url: str
    status_code: int | None
    is_available: bool
    redirect_url: str | None = None
    error: str | None = None


class LinkChecker:
    """并发检查一批 URL 的可用性。"""

    def __init__(self, timeout: float = 10.0, max_concurrency: int = 5) -> None:
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def check_url(self, url: str) -> LinkCheckResult:
        """
        检查单个 URL 的可用性。

        策略：先 HEAD，405/403/401 时回退 GET（只取 headers，不下载 body）。
        200-399 视为可用。
        """
        normalized_url = normalize_external_url(url)
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; LinkChecker/1.0)"},
                ) as client:
                    # 先尝试 HEAD
                    resp = await client.head(normalized_url)
                    if resp.status_code in (405, 403, 401):
                        # HEAD 不被允许或被拦截，回退 GET
                        resp = await client.get(
                            normalized_url,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                "Range": "bytes=0-1024",
                            },
                        )

                    final_url = normalize_external_url(str(resp.url))
                    redirect_url = final_url if final_url != normalized_url else None
                    is_available = 200 <= resp.status_code < 400

                    return LinkCheckResult(
                        url=url,
                        status_code=resp.status_code,
                        is_available=is_available,
                        redirect_url=redirect_url,
                    )
            except httpx.TimeoutException:
                return LinkCheckResult(url=url, status_code=None, is_available=False, error="timeout")
            except Exception as exc:
                return LinkCheckResult(url=url, status_code=None, is_available=False, error=str(exc)[:200])

    async def check_batch(self, urls: list[str]) -> list[LinkCheckResult]:
        """并发检查一批 URL。"""
        tasks = [self.check_url(url) for url in urls]
        return await asyncio.gather(*tasks)
