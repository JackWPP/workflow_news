from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.models import Article, Report, ReportItem
from app.services.eval_rubric import (
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER_PROMPT_TEMPLATE,
    WEIGHTS,
    compute_weighted_total,
    faithfulness_score_map,
)
from app.services.evaluation import compute_run_scores

logger = logging.getLogger(__name__)

try:
    from app.models import EvaluationRun  # type: ignore[attr-defined]
    _EVAL_MODEL_AVAILABLE = True
except ImportError:
    _EVAL_MODEL_AVAILABLE = False


class EvalRunner:
    def __init__(
        self,
        judge_model: str = "claude-opus-4-7",
        llm_client: Any = None,
    ):
        self.judge_model = judge_model
        self.llm_client = llm_client

    async def evaluate_report(
        self,
        session: Any,
        report: Report,
        articles: list[Article] | None = None,
    ) -> dict[str, Any]:
        if articles is None:
            articles = self._load_articles(session, report)

        prog_metrics = self._compute_programmatic_metrics(report, articles)

        judge_result: dict[str, Any] = {}
        if self.llm_client and self.llm_client.enabled:
            try:
                judge_result = await self._llm_judge_evaluate(
                    articles, list(report.items)
                )
            except Exception:
                logger.warning(
                    "LLM-as-Judge evaluation failed for report %s, skipping semantic eval",
                    report.id,
                )
        else:
            logger.warning(
                "LLM client not available, skipping LLM-as-Judge for report %s",
                report.id,
            )

        eval_run = self._persist_evaluation(
            session=session,
            report_id=report.id,
            judge_result=judge_result,
            prog_metrics=prog_metrics,
        )

        return {
            "eval_run_id": eval_run.get("id") if isinstance(eval_run, dict) else getattr(eval_run, "id", None),
            "weighted_total": judge_result.get("weighted_total"),
            "faithfulness": judge_result.get("faithfulness"),
            "faithfulness_reason": judge_result.get("faithfulness_reason"),
            "coverage": judge_result.get("coverage"),
            "coverage_reason": judge_result.get("coverage_reason"),
            "dedup": judge_result.get("dedup"),
            "dedup_reason": judge_result.get("dedup_reason"),
            "fluency": judge_result.get("fluency"),
            "fluency_reason": judge_result.get("fluency_reason"),
            "research_value": judge_result.get("research_value"),
            "research_value_reason": judge_result.get("research_value_reason"),
            "top_issues": judge_result.get("top_issues", []),
            "prog_metrics": prog_metrics,
        }

    def _load_articles(
        self, session: Any, report: Report
    ) -> list[Article]:
        if report.retrieval_run_id:
            articles = list(
                session.scalars(
                    select(Article).where(Article.run_id == report.retrieval_run_id)
                ).all()
            )
            if articles:
                return articles

        article_ids = {
            item.article_id
            for item in report.items
            if item.article_id is not None
        }
        if article_ids:
            return list(
                session.scalars(
                    select(Article).where(Article.id.in_(article_ids))
                ).all()
            )
        return []

    async def _llm_judge_evaluate(
        self,
        articles: list[Article],
        report_items: list[ReportItem],
    ) -> dict[str, Any]:
        articles_json = json.dumps(
            [
                {
                    "id": a.id,
                    "title": a.title,
                    "summary": a.summary,
                    "domain": a.domain,
                    "source_name": a.source_name,
                    "section": a.section,
                }
                for a in articles
            ],
            ensure_ascii=False,
        )
        items_json = json.dumps(
            [
                {
                    "id": it.id,
                    "title": it.title,
                    "summary": it.summary,
                    "research_signal": it.research_signal,
                    "section": it.section,
                    "source_name": it.source_name,
                }
                for it in report_items
            ],
            ensure_ascii=False,
        )

        prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            articles_json=articles_json,
            report_items_json=items_json,
        )

        response = await self.llm_client.simple_json_completion(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_content=prompt,
            temperature=0.1,
        )

        if not response:
            logger.warning("LLM-as-Judge returned empty response")
            return {}

        faithfulness = response.get("faithfulness", {})
        coverage = response.get("coverage", {})
        dedup = response.get("dedup", {})
        fluency = response.get("fluency", {})
        research_value = response.get("research_value", {})

        raw_scores = {
            "faithfulness": int(faithfulness.get("score", 0)),
            "coverage": int(coverage.get("score", 0)),
            "dedup": int(dedup.get("score", 0)),
            "fluency": int(fluency.get("score", 0)),
            "research_value": int(research_value.get("score", 0)),
        }

        weighted_total = response.get("weighted_total")
        if weighted_total is None and any(raw_scores.values()):
            weighted_total = compute_weighted_total(raw_scores)

        claim_verification = response.get("claim_verification", {})
        total_claims = (
            len(claim_verification.get("supported", []))
            + len(claim_verification.get("unsupported", []))
            + len(claim_verification.get("not_found", []))
        )
        supported_claims = len(claim_verification.get("supported", []))
        ratio = supported_claims / total_claims if total_claims > 0 else 1.0

        if "score" not in faithfulness:
            faithfulness["score"] = faithfulness_score_map(ratio)

        return {
            "weighted_total": weighted_total,
            "faithfulness": faithfulness.get("score"),
            "faithfulness_reason": faithfulness.get("reason", ""),
            "coverage": coverage.get("score"),
            "coverage_reason": coverage.get("reason", ""),
            "dedup": dedup.get("score"),
            "dedup_reason": dedup.get("reason", ""),
            "fluency": fluency.get("score"),
            "fluency_reason": fluency.get("reason", ""),
            "research_value": research_value.get("score"),
            "research_value_reason": research_value.get("reason", ""),
            "top_issues": response.get("top_issues", []),
            "extracted_claims": response.get("extracted_claims", []),
            "claim_verification": claim_verification,
            "total_claims": total_claims,
            "supported_claims": supported_claims,
            "faithfulness_ratio": round(ratio, 4),
            "judge_raw_output": response,
        }

    def _compute_programmatic_metrics(
        self, report: Report, articles: list[Article]
    ) -> dict[str, Any]:
        payload = (
            report.retrieval_run.debug_payload
            if report.retrieval_run and report.retrieval_run.debug_payload
            else {}
        ) or {}

        scores = compute_run_scores(payload, report_status=report.status)

        selected_ids = {item.article_id for item in report.items if item.article_id}
        article_ids = {a.id for a in articles}

        true_positives = selected_ids & article_ids
        precision = (
            len(true_positives) / len(selected_ids) if selected_ids else 0.0
        )
        recall = (
            len(true_positives) / len(article_ids) if article_ids else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        return {
            **scores,
            "precision_at_k": round(precision, 4),
            "recall_at_k": round(recall, 4),
            "f1_at_k": round(f1, 4),
            "article_pool_size": len(articles),
            "report_item_count": len(report.items),
        }

    def _persist_evaluation(
        self,
        session: Any,
        report_id: int,
        judge_result: dict[str, Any],
        prog_metrics: dict[str, Any],
    ) -> Any:
        if not _EVAL_MODEL_AVAILABLE:
            logger.warning(
                "EvaluationRun model not available, skipping persistence for report %s",
                report_id,
            )
            return {}

        try:
            claim_verification = judge_result.get("claim_verification", {})
            total_claims = judge_result.get("total_claims", 0)
            supported_claims = judge_result.get("supported_claims", 0)
            if not total_claims and claim_verification:
                total_claims = (
                    len(claim_verification.get("supported", []))
                    + len(claim_verification.get("unsupported", []))
                    + len(claim_verification.get("not_found", []))
                )
                supported_claims = len(claim_verification.get("supported", []))

            eval_run = EvaluationRun(
                report_id=report_id,
                judge_model=self.judge_model,
                evaluated_at=datetime.now(UTC),
                faithfulness_score=judge_result.get("faithfulness"),
                coverage_score=judge_result.get("coverage"),
                dedup_score=judge_result.get("dedup"),
                fluency_score=judge_result.get("fluency"),
                research_value_score=judge_result.get("research_value"),
                weighted_total=judge_result.get("weighted_total"),
                total_claims=total_claims,
                supported_claims=supported_claims,
                faithfulness_ratio=judge_result.get("faithfulness_ratio"),
                precision_at_k=prog_metrics.get("precision_at_k"),
                recall_at_k=prog_metrics.get("recall_at_k"),
                judge_raw_output=judge_result.get("judge_raw_output"),
                top_issues=judge_result.get("top_issues", []),
            )
            session.add(eval_run)
            session.flush()
            logger.info(
                "EvaluationRun %s persisted for report %s, weighted_total=%s",
                eval_run.id,
                report_id,
                eval_run.weighted_total,
            )
            return eval_run
        except Exception:
            logger.warning(
                "Failed to persist EvaluationRun for report %s",
                report_id,
                exc_info=True,
            )
            return {}
