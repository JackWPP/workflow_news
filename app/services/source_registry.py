from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SOURCES_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"


def load_sources_from_yaml(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or _SOURCES_PATH
    if not path.exists():
        logger.warning("sources.yaml not found at %s, returning empty list", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            logger.warning("sources.yaml is not a list, returning empty")
            return []
        return data
    except Exception as exc:
        logger.warning("Failed to load sources.yaml: %s", exc)
        return []


def get_rss_sources(sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if s.get("kind") == "rss" and s.get("enabled", True)]


def get_api_sources(sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if s.get("kind") == "api" and s.get("enabled", True)]


def get_search_sources(sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if s.get("kind") == "search" and s.get("enabled", True)]


def get_listing_sources(sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if s.get("kind") == "listing" and s.get("enabled", True)]


def get_sources_by_tier(tier: str, sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if s.get("tier") == tier and s.get("enabled", True)]


def get_sources_by_section(section: str, sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [s for s in sources if section in (s.get("sections") or []) and s.get("enabled", True)]


def get_arxiv_queries(sources: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if sources is None:
        sources = load_sources_from_yaml()
    return [
        s for s in sources
        if s.get("kind") == "api" and s.get("api_type") == "arxiv" and s.get("enabled", True)
    ]
