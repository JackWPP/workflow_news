from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.jina_reader import JinaReaderClient, _score_image_src


class TestScoreImageSrc:
    def test_reject_logo(self):
        assert _score_image_src("https://example.com/logo.png", "") == -1000

    def test_reject_icon(self):
        assert _score_image_src("https://example.com/icon-32x32.png", "") == -1000

    def test_reject_placeholder(self):
        assert _score_image_src("https://example.com/placeholder.jpg", "") == -1000

    def test_content_image_bonus(self):
        score = _score_image_src("https://example.com/content/article-image.jpg", "")
        assert score > 5

    def test_large_image_bonus(self):
        score = _score_image_src("https://example.com/image-1200x800.jpg", "")
        assert score > 5

    def test_svg_penalty(self):
        score = _score_image_src("https://example.com/vector.svg", "")
        assert score < 5

    def test_article_context_bonus(self):
        html = '<html><body><article><img src="https://example.com/photo.jpg"></article></body></html>'
        score = _score_image_src("https://example.com/photo.jpg", html)
        assert score > 10

    def test_main_context_bonus(self):
        html = '<html><body><main><img src="https://example.com/image.png"></main></body></html>'
        score = _score_image_src("https://example.com/image.png", html)
        assert score > 10


def _make_jina_mock(jina_response: dict):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = jina_response
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestJinaReaderScrape:
    @pytest.mark.asyncio
    async def test_read_page_success(self):
        client = JinaReaderClient(api_key="test-key")
        jina_response = {
            "data": {
                "title": "Polymer News",
                "content": "# Polymer Recycling\n\nNew breakthrough in recycling...",
                "images": {"https://example.com/image.jpg": {}},
                "publishedTime": "2026-01-15T10:00:00Z",
            }
        }
        mock_client = _make_jina_mock(jina_response)
        with patch("app.services.jina_reader.httpx.AsyncClient", return_value=mock_client):
            result = await client.scrape("https://example.com/article")
            assert result["title"] == "Polymer News"
            assert result["status"] == "success"
            assert result["scrape_layer"] == "jina"
            assert "Polymer Recycling" in result["markdown"]

    @pytest.mark.asyncio
    async def test_read_page_timeout_fallback(self):
        client = JinaReaderClient(api_key="test-key")
        import httpx

        call_count = 0

        def client_factory(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                mock = MagicMock()
                mock.get = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
                mock.__aenter__ = AsyncMock(return_value=mock)
                mock.__aexit__ = AsyncMock(return_value=False)
                return mock
            fallback_resp = MagicMock()
            fallback_resp.status_code = 200
            fallback_resp.text = "<html><head><title>Test</title></head><body><p>Content</p></body></html>"
            fallback_resp.url = "https://example.com/article"
            fallback_resp.raise_for_status = MagicMock()
            mock = MagicMock()
            mock.get = AsyncMock(return_value=fallback_resp)
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            return mock

        with patch("app.services.jina_reader.httpx.AsyncClient", side_effect=client_factory):
            result = await client.scrape("https://example.com/article", timeout_seconds=1)
            assert result["scrape_layer"] == "direct_http"


class TestJinaReaderImageExtraction:
    @pytest.mark.asyncio
    async def test_image_from_jina_images_dict(self):
        client = JinaReaderClient(api_key="test-key")
        jina_response = {
            "data": {
                "title": "Test",
                "content": "Some content here with enough text",
                "images": {
                    "https://example.com/content-photo.jpg": {},
                    "https://example.com/logo.png": {},
                },
            }
        }
        mock_client = _make_jina_mock(jina_response)
        with patch("app.services.jina_reader.httpx.AsyncClient", return_value=mock_client):
            result = await client.scrape("https://example.com/article")
            assert result["image_url"] is not None
            assert "logo" not in result["image_url"]

    @pytest.mark.asyncio
    async def test_image_from_markdown(self):
        client = JinaReaderClient(api_key="test-key")
        jina_response = {
            "data": {
                "title": "Test",
                "content": "![Photo](https://example.com/article-photo.jpg)\n\nSome text content here",
                "images": None,
            }
        }
        mock_client = _make_jina_mock(jina_response)
        with patch("app.services.jina_reader.httpx.AsyncClient", return_value=mock_client):
            result = await client.scrape("https://example.com/article")
            assert result["image_url"] == "https://example.com/article-photo.jpg"


class TestJinaReaderContentExtraction:
    @pytest.mark.asyncio
    async def test_content_extraction(self):
        client = JinaReaderClient(api_key="test-key")
        jina_response = {
            "data": {
                "title": "Polymer Research",
                "content": "## Abstract\n\nThis study explores new polymer recycling methods.",
                "publishedTime": "2026-01-15T10:00:00Z",
            }
        }
        mock_client = _make_jina_mock(jina_response)
        with patch("app.services.jina_reader.httpx.AsyncClient", return_value=mock_client):
            result = await client.scrape("https://example.com/article")
            assert "polymer recycling" in result["markdown"].lower()
            assert result["published_at"] is not None
