from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.search_engine import SearchEngine


class TestSearchEngineBatchSearch:
    @pytest.mark.asyncio
    async def test_batch_search_preserves_discovery_query_metadata(self):
        engine = SearchEngine()
        engine.search = AsyncMock(
            side_effect=[
                [
                    {
                        "url": "https://example.com/a",
                        "title": "A",
                        "snippet": "polymer",
                        "domain": "example.com",
                    }
                ],
                [
                    {
                        "url": "https://example.com/b",
                        "title": "B",
                        "snippet": "plastics",
                        "domain": "example.com",
                    }
                ],
            ]
        )

        rows = await engine.batch_search(["query one", "query two"], language="en")

        assert rows[0]["search_query"] == "query one"
        assert rows[0]["metadata"]["search_query"] == "query one"
        assert rows[0]["metadata"]["language"] == "en"
        assert rows[1]["search_query"] == "query two"
