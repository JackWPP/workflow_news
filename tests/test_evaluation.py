from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.evaluation import (
    build_evaluation_summary,
    compute_run_scores,
    enrich_debug_payload,
    run_offline_benchmark,
)


def _load_synthetic_cases():
    path = Path(__file__).parent / "eval_sets" / "v0_synthetic.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("items", [])


class TestComputeRunScores:
    def test_complete_golden_run(self):
        scores = compute_run_scores(
            {
                "selected_count": 4,
                "section_coverage": 3,
                "image_selected_count": 3,
                "publishable_count": 4,
                "borderline_count": 0,
                "publish_grade": "complete",
                "off_topic_escape_count": 0,
                "round_count": 1,
            }
        )
        assert scores["content_score"] >= 80
        assert scores["daily_report_score"] >= 75

    def test_failed_empty_run(self):
        scores = compute_run_scores(
            {
                "selected_count": 0,
                "section_coverage": 0,
                "image_selected_count": 0,
                "publishable_count": 0,
                "borderline_count": 0,
                "publish_grade": "failed",
            }
        )
        assert scores["daily_report_score"] <= 50

    def test_missing_keys_default_safely(self):
        scores = compute_run_scores({})
        assert scores["content_score"] >= 0
        assert scores["daily_report_score"] >= 0
        assert isinstance(scores["round2_recovery"], bool)

    def test_partial_with_fallbacks_drops_stability(self):
        base = compute_run_scores(
            {"selected_count": 3, "section_coverage": 2, "image_selected_count": 2,
             "publishable_count": 3, "publish_grade": "complete", "round_count": 1}
        )
        degraded = compute_run_scores(
            {"selected_count": 3, "section_coverage": 2, "image_selected_count": 2,
             "publishable_count": 3, "publish_grade": "degraded",
             "fallbacks_triggered": ["provider_error", "timeout"], "round_count": 2}
        )
        assert degraded["stability_score"] < base["stability_score"]

    def test_off_topic_escape_hurts_relevance(self):
        clean = compute_run_scores(
            {"selected_count": 3, "section_coverage": 2, "publishable_count": 3,
             "publish_grade": "partial", "off_topic_escape_count": 0}
        )
        dirty = compute_run_scores(
            {"selected_count": 3, "section_coverage": 2, "publishable_count": 2,
             "borderline_count": 1, "publish_grade": "partial", "off_topic_escape_count": 2}
        )
        assert dirty["relevance_score"] < clean["relevance_score"]

    def test_all_borderline_triggers_low_relevance(self):
        scores = compute_run_scores(
            {"selected_count": 2, "section_coverage": 1, "publishable_count": 0,
             "borderline_count": 2, "publish_grade": "hold_for_missing_quality"}
        )
        assert scores["relevance_score"] <= 60

    def test_image_quality_impacts_score(self):
        good_images = compute_run_scores(
            {"selected_count": 3, "section_coverage": 3, "image_selected_count": 3,
             "publishable_count": 3, "publish_grade": "complete",
             "image_candidate_count": 3, "image_rejections": {}}
        )
        bad_images = compute_run_scores(
            {"selected_count": 3, "section_coverage": 3, "image_selected_count": 1,
             "publishable_count": 3, "publish_grade": "partial",
             "image_candidate_count": 8,
             "image_rejections": {"logo_detected": 3, "low_resolution": 4}}
        )
        assert bad_images["image_score"] < good_images["image_score"]


class TestBenchmark:
    def test_all_synthetic_cases_pass(self):
        items = _load_synthetic_cases()
        assert len(items) >= 12

        for case in items:
            scores = compute_run_scores(
                case["payload"],
                report_status=case["payload"].get("publish_grade"),
            )
            expect = case["expect"]
            for key, value in expect.items():
                if key.endswith("_min"):
                    assert scores[key.replace("_min", "")] >= value, (
                        f"{case['name']}: {key.replace('_min', '')} "
                        f"({scores[key.replace('_min', '')]}) < {value}"
                    )
                elif key.endswith("_max"):
                    assert scores[key.replace("_max", "")] <= value, (
                        f"{case['name']}: {key.replace('_max', '')} "
                        f"({scores[key.replace('_max', '')]}) > {value}"
                    )
                elif key == "image_lt_content":
                    assert scores["image_score"] < scores["content_score"], (
                        f"{case['name']}: image_score ({scores['image_score']}) "
                        f">= content_score ({scores['content_score']})"
                    )
                elif key == "round2_recovery":
                    assert scores["round2_recovery"] is value, (
                        f"{case['name']}: round2_recovery expected {value}"
                    )

    def test_benchmark_function_returns_expected_structure(self):
        result = run_offline_benchmark()
        assert "benchmark_score" in result
        assert "benchmark_pass_rate" in result
        assert "cases" in result
        assert len(result["cases"]) == 5
        assert 0 <= result["benchmark_score"] <= 100
        assert 0 <= result["benchmark_pass_rate"] <= 1

    def test_benchmark_pass_rate_is_one(self):
        result = run_offline_benchmark()
        assert result["benchmark_pass_rate"] == 1.0


class TestEnrichDebugPayload:
    def test_enrich_adds_scores(self):
        enriched = enrich_debug_payload({"selected_count": 3, "section_coverage": 2})
        assert "content_score" in enriched
        assert "daily_report_score" in enriched

    def test_enrich_empty_payload(self):
        enriched = enrich_debug_payload(None)
        assert isinstance(enriched, dict)
        assert "content_score" in enriched


class TestBuildEvaluationSummary:
    def test_returns_expected_structure(self):
        from app.database import session_scope

        with session_scope() as session:
            summary = build_evaluation_summary(session, days=365)
            assert "recent_runs" in summary
            assert "benchmark" in summary
            assert "report_samples" in summary
            assert isinstance(summary["recent_runs"], list)
            assert isinstance(summary["benchmark"], dict)


def _compute_precision_recall_f1(retrieved_ids, relevant_ids):
    if not retrieved_ids:
        return 0.0, 0.0, 0.0
    tp = len(retrieved_ids & relevant_ids)
    precision = tp / len(retrieved_ids)
    recall = tp / len(relevant_ids) if relevant_ids else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


class TestPrecisionRecall:
    def test_perfect_match(self):
        p, r, f1 = _compute_precision_recall_f1({1, 2, 3}, {1, 2, 3})
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_half_match(self):
        p, r, f1 = _compute_precision_recall_f1({1, 2}, {1, 2, 3, 4})
        assert p == 1.0
        assert r == 0.5

    def test_empty_retrieved(self):
        p, r, f1 = _compute_precision_recall_f1(set(), {1, 2, 3})
        assert p == 0.0
        assert r == 0.0

    def test_empty_relevant(self):
        p, r, f1 = _compute_precision_recall_f1({1, 2, 3}, set())
        assert p == 0.0
        assert r == 0.0
