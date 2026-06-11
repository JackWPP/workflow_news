from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.bocha_search import BochaSearchClient


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = ""
    return resp


def _bocha_web_response(items=None):
    return {
        "code": 200,
        "data": {
            "webPages": {
                "value": items or [
                    {
                        "url": "https://example.com/a1",
                        "name": "Polymer News",
                        "snippet": "Recycling breakthrough",
                        "datePublished": "2026-01-15T10:00:00Z",
                        "siteName": "example.com",
                    }
                ]
            }
        },
    }


def _bocha_ai_response(messages=None):
    return {
        "code": 200,
        "data": {
            "answer": "AI summary of results",
            "question": ["Follow-up 1?"],
            "messages": messages or [
                {
                    "content_type": "webpage",
                    "content": '{"value": [{"url": "https://example.com/a1", "name": "Test", "snippet": "snippet", "datePublished": "2026-01-15T10:00:00Z", "siteName": "example.com"}]}',
                }
            ],
        },
    }


class TestBochaWebSearch:
    @pytest.mark.asyncio
    async def test_web_search(self):
        client = BochaSearchClient(api_key="test-key")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, _bocha_web_response()))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.bocha_search.httpx.AsyncClient", return_value=mock_client):
            results = await client.search("polymer recycling")
            assert len(results) == 1
            assert results[0]["url"] == "https://example.com/a1"
            assert results[0]["provider"] == "bocha"

    @pytest.mark.asyncio
    async def test_web_search_disabled(self):
        client = BochaSearchClient(api_key="")
        results = await client.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_web_search_http_error(self):
        client = BochaSearchClient(api_key="test-key")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(429))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.bocha_search.httpx.AsyncClient", return_value=mock_client):
            results = await client.search("test")
            assert results == []
            assert client._failure_count == 1


class TestBochaAiSearch:
    @pytest.mark.asyncio
    async def test_ai_search(self):
        client = BochaSearchClient(api_key="test-key")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, _bocha_ai_response()))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.bocha_search.httpx.AsyncClient", return_value=mock_client):
            results = await client.ai_search("polymer research")
            assert len(results) == 1
            assert results[0].get("ai_answer") == "AI summary of results"

    @pytest.mark.asyncio
    async def test_ai_search_disabled(self):
        client = BochaSearchClient(api_key="")
        results = await client.ai_search("test")
        assert results == []


class TestBochaSearchWithFreshness:
    @pytest.mark.asyncio
    async def test_freshness_parameter(self):
        client = BochaSearchClient(api_key="test-key")
        mock_client = AsyncMock()
        captured_payload = {}

        async def capture_post(url, json=None, headers=None):
            captured_payload.update(json)
            return _mock_response(200, _bocha_web_response())

        mock_client.post = AsyncMock(side_effect=capture_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.bocha_search.httpx.AsyncClient", return_value=mock_client):
            await client.search("test", freshness="oneDay")
            assert captured_payload.get("freshness") == "oneDay"


class TestBochaSearchWithInclude:
    @pytest.mark.asyncio
    async def test_include_domains(self):
        client = BochaSearchClient(api_key="test-key")
        mock_client = AsyncMock()
        captured_payload = {}

        async def capture_post(url, json=None, headers=None):
            captured_payload.update(json)
            return _mock_response(200, _bocha_web_response())

        mock_client.post = AsyncMock(side_effect=capture_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.bocha_search.httpx.AsyncClient", return_value=mock_client):
            await client.search("test", include_domains=["nature.com", "sciencedirect.com"])
            assert captured_payload.get("include") == "nature.com|sciencedirect.com"


class TestBochaHealthSnapshot:
    def test_healthy_state(self):
        client = BochaSearchClient(api_key="test-key")
        snap = client.health_snapshot()
        assert snap["state"] == "healthy"
        assert snap["enabled"] is True

    def test_disabled_state(self):
        client = BochaSearchClient(api_key="")
        snap = client.health_snapshot()
        assert snap["health_state"] == "disabled"
