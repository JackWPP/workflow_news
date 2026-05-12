from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.database import session_scope
from app.models import ArticlePool, Report

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
        self._dedup = SemanticDedup(
            api_key=settings.siliconflow_api_key,
            embedding_model_name=settings.siliconflow_embedding_model,
        )


    async def gather_seeds(self, target_date: date | None = None) -> list[dict[str, Any]]:
        """Pull articles from ArticlePool with URL+MinHash dedup only. No LLM evaluation."""
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

        # Separate by language for dedup
        zh = [a for a in articles if a.language == "zh"]
        en = [a for a in articles if a.language == "en"]

        results = []
        for lang_articles in [zh, en]:
            urls = [a.url for a in lang_articles]
            url_indices = self._dedup.url_dedup(urls)
            deduped = [lang_articles[i] for i in url_indices]

            texts = [f"{a.title} {a.summary or ''}" for a in deduped]
            minhash_indices = self._dedup.minhash_dedup(texts)
            final = [deduped[i] for i in minhash_indices]

            for a in final[:15]:  # max 15 per language
                results.append({
                    "url": a.url, "title": a.title, "domain": a.domain,
                    "snippet": a.summary or "", "published_at": a.published_at,
                    "language": a.language, "source_type": a.source_type,
                })

        # Sort: RSS first, then template search
        results.sort(key=lambda x: 0 if x["source_type"] == "rss" else 1)
        return results[:30]

    # UNUSED: kept for reference in simplified agent-driven flow
    async def _filter_and_evaluate(
        self, articles: list[dict], language: str,
    ) -> list[dict[str, Any]]:
        n_initial = len(articles)

        # Level 1: URL dedup
        urls = [a["url"] for a in articles]
        indices = self._dedup.url_dedup(urls)
        unique_articles = [articles[i] for i in indices]
        n_after_url = len(unique_articles)
        logger.info("URL去重: %d→%d", n_initial, n_after_url)

        if not unique_articles:
            return []

        # Level 2: MinHash dedup
        texts = [f"{a['title']} {a.get('snippet', '')}" for a in unique_articles]
        minhash_indices = self._dedup.minhash_dedup(texts)
        minhash_articles = [unique_articles[i] for i in minhash_indices]
        n_after_minhash = len(minhash_articles)
        logger.info("MinHash去重: %d→%d", n_after_url, n_after_minhash)

        if not minhash_articles:
            return []

        # Level 3: Embedding semantic dedup (SiliconFlow API, graceful degradation)
        minhash_texts = [f"{a['title']} {a.get('snippet', '')}" for a in minhash_articles]
        final_articles = minhash_articles
        n_weak = 0
        if len(minhash_texts) <= 2:
            logger.info("Embedding去重跳过（仅%d条）", len(minhash_texts))
        else:
            try:
                unique_indices, weak_indices = await self._dedup.semantic_dedup(minhash_texts)
                final_articles = [minhash_articles[i] for i in unique_indices]
                n_weak = len(weak_indices)
                n_after_embedding = len(final_articles)
                logger.info("Embedding去重: %d→%d (弱相似: %d)", n_after_minhash, n_after_embedding, n_weak)
            except Exception as exc:
                logger.warning("Embedding去重失败，回退到MinHash结果: %s", exc)

        if not final_articles:
            return []

        evaluated = await self._evaluator.evaluate_batch(
            final_articles, language=language, batch_size=8, max_articles=_TOP_K_AFTER_EVAL,
        )
        result = evaluated if isinstance(evaluated, list) else []

        # 回写评估结果到 ArticlePool
        if result:
            try:
                with session_scope() as session:
                    for item in result:
                        item_url = item.get("url")
                        if not item_url:
                            continue
                        stmt = select(ArticlePool).where(ArticlePool.url == item_url)
                        article = session.scalars(stmt).first()
                        if article:
                            article.quality_score = item.get("quality_score")
                            article.section = item.get("section")
                            article.category = item.get("category")
                            article.eval_metadata = {
                                "key_finding": item.get("key_finding"),
                                "evaluated_at": now_local().isoformat(),
                            }
                    session.commit()
            except Exception as exc:
                logger.warning("Failed to write back evaluation results to ArticlePool: %s", exc)

        return result
