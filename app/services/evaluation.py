from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, select

from app.models import Report, RetrievalRun
from app.utils import now_local


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def compute_run_scores(payload: dict[str, Any] | None, report_status: str | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    selected_count = int(payload.get("selected_count", 0) or 0)
    section_coverage = int(payload.get("section_coverage", 0) or 0)
    verified_image_count = int(payload.get("image_selected_count", 0) or 0)
    round_count = int(payload.get("round_count", 1) or 1)
    publishable_count = int(payload.get("publishable_count", selected_count) or 0)
    borderline_count = int(payload.get("borderline_count", max(selected_count - publishable_count, 0)) or 0)
    off_topic_escape_count = int(payload.get("off_topic_escape_count", 0) or 0)
    fallback_count = len(payload.get("fallbacks_triggered") or [])
    provider_error_count = len(payload.get("provider_errors") or [])
    total_duration = sum(float(value or 0.0) for value in (payload.get("stage_durations") or {}).values())
    image_candidate_count = int(payload.get("image_candidate_count", 0) or 0)
    image_rejections = payload.get("image_rejections") or {}
    total_image_rejections = sum(int(value or 0) for value in image_rejections.values())
    publish_grade = str(payload.get("publish_grade") or report_status or "failed")

    selected_component = _clamp01(selected_count / 3.0)
    section_component = _clamp01(section_coverage / 2.0)
    publishable_ratio = _clamp01(publishable_count / max(selected_count, 1))
    content_score = _round_score(100 * (0.45 * selected_component + 0.35 * section_component + 0.20 * publishable_ratio))

    image_target_component = _clamp01(verified_image_count / 2.0)
    image_coverage_component = _clamp01(verified_image_count / max(selected_count, 1))
    image_quality_component = 1.0 - _clamp01(total_image_rejections / max(image_candidate_count, 1)) if image_candidate_count else 0.0
    image_score = _round_score(100 * (0.45 * image_target_component + 0.35 * image_coverage_component + 0.20 * image_quality_component))

    weak_ratio = _clamp01(borderline_count / max(selected_count, 1))
    off_topic_penalty = _clamp01(off_topic_escape_count / max(selected_count, 1))
    relevance_score = _round_score(100 * (0.60 * (1.0 - off_topic_penalty) + 0.40 * (1.0 - weak_ratio)))

    duration_component = 1.0 - _clamp01(total_duration / 360.0)
    fallback_component = 1.0 - _clamp01((fallback_count + provider_error_count) / 4.0)
    status_component = {
        "complete": 1.0,
        "complete_auto_publish": 1.0,
        "partial": 0.82,
        "partial_auto_publish": 0.82,
        "degraded": 0.55,
        "hold_for_missing_quality": 0.55,
        "failed": 0.15,
    }.get(publish_grade, 0.45)
    stability_score = _round_score(100 * (0.40 * status_component + 0.35 * fallback_component + 0.25 * duration_component))

    daily_report_score = _round_score(
        0.35 * content_score
        + 0.30 * image_score
        + 0.20 * relevance_score
        + 0.15 * stability_score
    )

    round2_recovery = bool(
        round_count >= 2
        and publish_grade in {"partial", "complete", "partial_auto_publish", "complete_auto_publish"}
        and (selected_count >= 2 or section_coverage >= 2 or verified_image_count >= 2)
    )

    return {
        "content_score": content_score,
        "image_score": image_score,
        "relevance_score": relevance_score,
        "stability_score": stability_score,
        "daily_report_score": daily_report_score,
        "publishable_count": publishable_count,
        "borderline_count": borderline_count,
        "round2_recovery": round2_recovery,
        "off_topic_escape_count": off_topic_escape_count,
    }


def enrich_debug_payload(payload: dict[str, Any] | None, report_status: str | None = None) -> dict[str, Any]:
    enriched = dict(payload or {})
    enriched.update(compute_run_scores(enriched, report_status=report_status))
    return enriched


def run_offline_benchmark() -> dict[str, Any]:
    today = now_local().date().isoformat()
    cases = [
        {
            "name": "policy_missing_time",
            "payload": {
                "selected_count": 1,
                "section_coverage": 1,
                "image_selected_count": 0,
                "publishable_count": 1,
                "borderline_count": 0,
                "publish_grade": "partial",
                "policy_gap_reason": "policy_missing_published_at",
            },
            "expect": lambda scores: scores["content_score"] >= 35 and scores["relevance_score"] >= 60,
            "note": "强政策稿缺时间时，内容仍应被记录，但不能伪装成 complete。",
        },
        {
            "name": "industry_borderline_image",
            "payload": {
                "selected_count": 2,
                "section_coverage": 1,
                "image_selected_count": 1,
                "publishable_count": 1,
                "borderline_count": 1,
                "publish_grade": "partial",
                "image_gap_reason": "insufficient_verified_images",
            },
            "expect": lambda scores: scores["image_score"] < scores["content_score"],
            "note": "边缘条目和弱图应被记作图片缺口，而不是误判为稳定 complete。",
        },
        {
            "name": "academic_strong_content_weak_image",
            "payload": {
                "selected_count": 2,
                "section_coverage": 2,
                "image_selected_count": 0,
                "publishable_count": 2,
                "borderline_count": 0,
                "publish_grade": "partial",
                "image_gap_reason": "no_verified_images",
            },
            "expect": lambda scores: scores["content_score"] >= 55 and scores["image_score"] <= 20,
            "note": "强内容但弱图时，内容得分应保住，图片得分明显偏低。",
        },
        {
            "name": "off_topic_and_pr_rejected",
            "payload": {
                "selected_count": 0,
                "section_coverage": 0,
                "image_selected_count": 0,
                "publishable_count": 0,
                "borderline_count": 0,
                "publish_grade": "failed",
                "rejection_counts": {
                    "off_topic_candidate": 6,
                    "pr_like_candidate": 4,
                    "blocked_domain": 2,
                },
                "off_topic_escape_count": 0,
            },
            "expect": lambda scores: scores["relevance_score"] >= 80,
            "note": "跑题稿和 PR 稿被挡住，本身应视为相关性控制成功。",
        },
        {
            "name": "duplicate_event_collapsed",
            "payload": {
                "selected_count": 2,
                "section_coverage": 2,
                "image_selected_count": 2,
                "publishable_count": 2,
                "borderline_count": 0,
                "publish_grade": "partial",
                "duplicate_ratio": 0.55,
                "round_count": 2,
            },
            "expect": lambda scores: scores["round2_recovery"] is True and scores["stability_score"] >= 45,
            "note": "重复事件被压缩后，第二轮补救仍然要能恢复出可发布版本。",
        },
    ]

    results: list[dict[str, Any]] = []
    total_score = 0.0
    passed_count = 0
    for case in cases:
        scores = compute_run_scores(case["payload"], report_status=case["payload"].get("publish_grade"))
        passed = bool(case["expect"](scores))
        passed_count += int(passed)
        total_score += scores["daily_report_score"]
        results.append(
            {
                "name": case["name"],
                "passed": passed,
                "note": case["note"],
                "daily_report_score": scores["daily_report_score"],
                "content_score": scores["content_score"],
                "image_score": scores["image_score"],
                "relevance_score": scores["relevance_score"],
                "stability_score": scores["stability_score"],
            }
        )

    pass_rate = round(passed_count / max(len(cases), 1), 4)
    average_score = total_score / max(len(cases), 1)
    benchmark_score = _round_score(average_score * 0.6 + pass_rate * 40.0)

    return {
        "benchmark_score": benchmark_score,
        "benchmark_pass_rate": pass_rate,
        "cases": results,
        "benchmark_score_trend": [{"date": today, "score": benchmark_score}],
    }


def get_recent_run_snapshot(session, days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
    since = now_local() - timedelta(days=max(days - 1, 0))
    runs = list(
        session.scalars(
            select(RetrievalRun)
            .where(RetrievalRun.run_date >= since)
            .order_by(desc(RetrievalRun.id))
            .limit(limit)
        ).all()
    )
    snapshots: list[dict[str, Any]] = []
    for run in runs:
        payload = enrich_debug_payload(run.debug_payload, report_status=run.status)
        snapshots.append(
            {
                "run_id": run.id,
                "date": run.run_date.date().isoformat(),
                "status": run.status,
                "publish_grade": payload.get("publish_grade", run.status),
                "selected_count": int(payload.get("selected_count", 0) or 0),
                "section_coverage": int(payload.get("section_coverage", 0) or 0),
                "verified_image_count": int(payload.get("image_selected_count", 0) or 0),
                "round_count": int(payload.get("round_count", 1) or 1),
                "content_score": payload.get("content_score", 0.0),
                "image_score": payload.get("image_score", 0.0),
                "relevance_score": payload.get("relevance_score", 0.0),
                "stability_score": payload.get("stability_score", 0.0),
                "daily_report_score": payload.get("daily_report_score", 0.0),
                "policy_gap_reason": payload.get("policy_gap_reason"),
                "image_gap_reason": payload.get("image_gap_reason"),
            }
        )
    return snapshots


def build_evaluation_summary(session, days: int = 7) -> dict[str, Any]:
    recent_runs = get_recent_run_snapshot(session, days=days, limit=10)
    benchmark = run_offline_benchmark()

    reports = list(
        session.scalars(
            select(Report).order_by(desc(Report.report_date), desc(Report.id)).limit(2)
        ).all()
    )
    report_samples = [
        {
            "report_id": report.id,
            "report_date": report.report_date.isoformat(),
            "title": report.title,
            "publish_grade": report.publish_grade,
            "selected_count": len(report.items),
            "verified_image_count": report.image_review_summary.get("verified_image_count", 0),
            "sections": sorted({item.section for item in report.items}),
        }
        for report in reports
    ]

    latest_run = recent_runs[0] if recent_runs else None
    best_run = max(recent_runs, key=lambda item: item["daily_report_score"], default=None)
    worst_run = min(recent_runs, key=lambda item: item["daily_report_score"], default=None)

    return {
        "recent_runs": recent_runs,
        "latest_run": latest_run,
        "best_run": best_run,
        "worst_run": worst_run,
        "benchmark": benchmark,
        "report_samples": report_samples,
    }
