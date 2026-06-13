from __future__ import annotations

from typing import Any

import pytest

from app.services.search_router import SearchRouter


@pytest.mark.asyncio
async def test_search_router_cache_separates_domain_filters():
    class _StubBocha:
        enabled = True

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def search(self, query: str, **kwargs: Any):
            self.calls.append(kwargs)
            include = kwargs.get("include_domains") or ["example.com"]
            domain = include[0]
            return [
                {
                    "url": f"https://{domain}/article",
                    "title": "polymer update",
                    "snippet": "polymer processing",
                    "domain": domain,
                }
            ]

        def health_snapshot(self) -> dict[str, Any]:
            return {"health_state": "healthy"}

    bocha = _StubBocha()
    router = SearchRouter(bocha_client=bocha)

    first = await router.search(
        "polymer policy", include_domains=["gov.cn"], max_results=3
    )
    second = await router.search(
        "polymer policy", include_domains=["nature.com"], max_results=3
    )
    third = await router.search(
        "polymer policy", include_domains=["gov.cn"], max_results=3
    )

    assert first[0]["domain"] == "gov.cn"
    assert second[0]["domain"] == "nature.com"
    assert third[0]["domain"] == "gov.cn"
    assert len(bocha.calls) == 2

