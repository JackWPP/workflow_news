from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import session_scope
from app.models import ArticlePool, Report
from app.services.batch_evaluator import BatchEvaluator
from app.services.llm_client import LLMClient
from app.services.semantic_dedup import SemanticDedup
from app.utils import now_local

logger = logging.getLogger(__name__)

_LOOKBACK_HOURS = 72
_MAX_POOL_ARTICLES = 200
_TOP_K_AFTER_EVAL = 20


class DailyComposer:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or LLMClient()
        self._dedup = SemanticDedup()
        self._evaluator = BatchEvaluator(llm_client=self._llm_client)

    async def gather_candidates(self, target_date: date | None = None) -> list[dict[str, Any]]:
        target = target_date or now_local().date()
        since = target - timedelta(hours=_LOOKBACK_HOURS)
        articles: list[ArticlePool] = []
        try:
            with session_scope() as session:
                articles = list(
                    session.scalars(
                        select(ArticlePool)
                        .where(ArticlePool.ingested_at >= since)
                        .order_by(ArticlePool.ingested_at.desc())
                        .limit(_MAX_POOL_ARTICLES)
                    ).all()
                )
        except Exception as exc:
            logger.warning("DailyComposer: failed to fetch ArticlePool: %s", exc)
            return []

        if not articles:
            return []

        zh_articles = [
            {
                "url": a.url, "title": a.title, "domain": a.domain,
                "snippet": a.summary or "", "published_at": a.published_at,
                "language": "zh",
            }
            for a in articles if a.language == "zh"
        ]
        en_articles = [
            {
                "url": a.url, "title": a.title, "domain": a.domain,
                "snippet": a.summary or "", "published_at": a.published_at,
                "language": "en",
            }
            for a in articles if a.language == "en"
        ]

        all_candidates: list[dict[str, Any]] = []
        if zh_articles:
            zh_candidates = await self._filter_and_evaluate(zh_articles, language="zh")
            all_candidates.extend(zh_candidates)
        if en_articles:
            en_candidates = await self._filter_and_evaluate(en_articles, language="en")
            all_candidates.extend(en_candidates)

        return all_candidates

    async def _filter_and_evaluate(
        self, articles: list[dict], language: str,
    ) -> list[dict[str, Any]]:
        urls = [a["url"] for a in articles]
        unique_indices = self._dedup.url_dedup(urls)
        unique_articles = [articles[i] for i in unique_indices]

        if not unique_articles:
            return []

        evaluated = await self._evaluator.evaluate_batch(
            unique_articles, language=language, batch_size=8, max_articles=_TOP_K_AFTER_EVAL,
        )
        return evaluated if isinstance(evaluated, list) else []
