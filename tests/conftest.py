from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

_TEST_DB = Path(tempfile.gettempdir()) / f"workflow_news_test_{os.getpid()}.db"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB}")
os.environ.setdefault("SHADOW_MODE", "false")
os.environ.setdefault("OPENROUTER_API_KEY", "")


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    from app.bootstrap import init_db
    from app.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_db():
    from app.bootstrap import init_db
    from app.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    init_db()
    yield


@pytest.fixture
def db_session():
    from app.database import session_scope

    with session_scope() as session:
        yield session


@pytest.fixture
def mock_llm():
    class MockLLM:
        enabled = True

        async def simple_completion(self, prompt: str, **kwargs: Any) -> str:
            return '{"worthy": true, "section": "industry", "key_finding": "test"}'

        async def structured_completion(
            self, prompt: str, schema: dict, **kwargs: Any
        ) -> dict:
            return {
                "worthy": True,
                "section": "industry",
                "key_finding": "test",
            }

    return MockLLM()


@pytest.fixture
def mock_search():
    class MockSearch:
        async def search(self, query: str, **kwargs: Any) -> list[dict]:
            return [
                {
                    "url": "https://example.com/article1",
                    "title": "Test Article 1",
                    "snippet": "Test snippet 1",
                    "domain": "example.com",
                }
            ]

    return MockSearch()


@pytest.fixture
def mock_scraper():
    class MockScraper:
        async def scrape(self, url: str, **kwargs: Any) -> dict:
            return {
                "title": "Test Article",
                "markdown": "Test content",
                "html": "<p>Test content</p>",
            }

    return MockScraper()
