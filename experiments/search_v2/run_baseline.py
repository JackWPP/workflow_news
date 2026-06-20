"""V2 Phase 0+0.5+A 落地后基线运行脚本.

绕过 FastAPI 路由层直接调 DailyOrchestrator.run()，跑一次完整日报。
跑完后查 SQLite 拿 metrics，落档到 baseline_post_phase_a.json。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ── 配置日志（unbuffered） ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
# 降低一些 noisy logger 的级别
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from sqlalchemy import func, select, text  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.models import (  # noqa: E402
    ArticlePool,
    Report as ReportModel,
    ReportItem,
    RetrievalRun,
)
from app.services.daily_orchestrator import DailyOrchestrator  # noqa: E402


def _parse_dt(val):
    """尝试解析各种日期格式。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


async def main():
    print(f"=== V2 baseline run started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)

    # ── Step A: 模拟 _ensure_pool_fresh_before_report ─────
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    fresh_count = 0
    with session_scope() as session:
        fresh_count = session.scalar(
            select(func.count(ArticlePool.id)).where(ArticlePool.ingested_at >= cutoff)
        ) or 0

    ingest_decision = {
        "fresh_count_at_check": int(fresh_count),
        "min_fresh_threshold": 15,
        "ingester_triggered": fresh_count < 15,
        "trigger_reason": "fresh_count<15" if fresh_count < 15 else "pool_healthy",
        "checked_at": datetime.now(UTC).isoformat(),
    }
    print(f"[Pool prewarm] fresh_count={fresh_count}, will_trigger={ingest_decision['ingester_triggered']}", flush=True)

    if ingest_decision["ingester_triggered"]:
        print("[Pool prewarm] Triggering full ContinuousIngester.run()...", flush=True)
        from app.services.ingester import ContinuousIngester
        try:
            ingested = await ContinuousIngester().run()
            ingest_decision["ingester_ingested"] = ingested
            print(f"[Pool prewarm] Full ingester finished: {ingested} new articles", flush=True)
        except Exception as exc:
            ingest_decision["ingester_error"] = str(exc)[:300]
            print(f"[Pool prewarm] Ingester FAILED (non-fatal): {type(exc).__name__}: {exc}", flush=True)

    # ── Step B: 创建 RetrievalRun 记录 ─────────────────────
    target_date = date.today()
    with session_scope() as session:
        run_record = RetrievalRun(
            run_date=datetime.now(UTC),
            status="running",
        )
        session.add(run_record)
        session.flush()
        run_id = run_record.id
        session.commit()
    print(f"[DB] Created RetrievalRun id={run_id}", flush=True)

    # ── Step C: 跑 DailyOrchestrator ───────────────────────
    print(flush=True)
    print("[DailyOrchestrator] Starting...", flush=True)
    orch_start = time.perf_counter()
    result = {}
    try:
        orch = DailyOrchestrator()
        result = await orch.run(run_id=run_id)
        elapsed = round(time.perf_counter() - orch_start, 1)
        print(f"[DailyOrchestrator] Finished in {elapsed}s", flush=True)
        print(f"  meta: {result.get('meta', {})}", flush=True)
    except Exception as exc:
        elapsed = round(time.perf_counter() - orch_start, 1)
        print(f"[DailyOrchestrator] FAILED after {elapsed}s: {type(exc).__name__}: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        result = {"error": str(exc), "meta": {"elapsed_seconds": elapsed}, "cards": []}

    # ── Step D: 持久化 Report + ReportItem ─────────────────
    cards = result.get("cards", [])
    report_id = None
    report_status = None
    with session_scope() as session:
        report = ReportModel(
            report_date=target_date,
            status="complete_auto_publish" if len(cards) >= 4 else "partial_auto_publish",
            title=f"{target_date.isoformat()} 高分子加工全视界日报",
            markdown_content=result.get("html", ""),
            summary=result.get("summary", ""),
            pipeline_version="multi-agent-v1",
            retrieval_run_id=run_id,
        )
        session.add(report)
        session.flush()
        report_id = report.id
        report_status = report.status

        for i, card in enumerate(cards):
            item = ReportItem(
                report_id=report_id,
                section=card.get("section", "industry"),
                rank=i + 1,
                title=card.get("title", ""),
                source_name=card.get("source_name", ""),
                source_url=card.get("url", ""),
                published_at=_parse_dt(card.get("published_at")),
                summary=card.get("summary", ""),
                research_signal=card.get("why_selected", ""),
                image_url=card.get("image_url"),
                language=card.get("language", "zh"),
                decision_trace={
                    "category": card.get("category", "高材制造"),
                    "source_tier": card.get("source_tier", ""),
                    "source_kind": card.get("source_kind", ""),
                    "selection_reason": card.get("why_selected", ""),
                    "key_finding": card.get("key_finding", ""),
                },
            )
            session.add(item)

        # 更新 RetrievalRun 状态
        run_record = session.get(RetrievalRun, run_id)
        if run_record:
            run_record.status = "complete"
            run_record.finished_at = datetime.now(UTC)
        session.commit()
    print(f"[DB] Persisted Report id={report_id}, status={report_status}, {len(cards)} items", flush=True)

    # ── Step E: 查询数据库拿 metrics ───────────────────────
    print(flush=True)
    print("=== Querying DB for run metrics ===", flush=True)
    with session_scope() as session:
        rr = session.get(RetrievalRun, run_id)
        report = session.scalars(
            select(ReportModel).where(ReportModel.retrieval_run_id == run_id).limit(1)
        ).first()

        # ArticlePool source_type 分布（最近 1 小时）
        provider_dist = session.execute(text(
            "SELECT source_type, COUNT(*) FROM article_pool WHERE ingested_at >= datetime('now', '-1 hour') GROUP BY source_type"
        )).fetchall()

        # ArticlePool 总数
        pool_total = session.scalar(select(func.count(ArticlePool.id))) or 0

        # 最近 24h 入池数
        pool_24h = session.scalar(
            select(func.count(ArticlePool.id)).where(
                ArticlePool.ingested_at >= datetime.now(UTC) - timedelta(hours=24)
            )
        ) or 0

    # ── Step F: Bocha / Zhipu health_snapshot ──────────────
    # 注意：新建实例统计会清零，实际数据在日志里
    from app.services.bocha_search import BochaSearchClient
    from app.services.zhipu_search import ZhipuSearchClient
    bocha_snap = BochaSearchClient().health_snapshot()
    zhipu_snap = ZhipuSearchClient().health_snapshot()

    # ── Step G: 落档 ───────────────────────────────────────
    baseline = {
        "run_started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ingest_decision": ingest_decision,
        "orchestrator_meta": result.get("meta", {}),
        "orchestrator_error": result.get("error"),
        "retrieval_run_id": run_id,
        "retrieval_status": rr.status if rr else None,
        "retrieval_created_at": str(rr.created_at) if rr and rr.created_at else None,
        "retrieval_finished_at": str(rr.finished_at) if rr and rr.finished_at else None,
        "retrieval_duration_seconds": (
            (rr.finished_at - rr.created_at).total_seconds()
            if rr and rr.created_at and rr.finished_at else None
        ),
        "report_id": report_id,
        "report_status": report_status,
        "report_publish_grade": report.publish_grade if report else None,
        "report_title": report.title if report else None,
        "report_item_count": len(report.items) if report else 0,
        "report_summary_length": len(report.summary) if report and report.summary else 0,
        "report_markdown_length": len(report.markdown_content) if report and report.markdown_content else 0,
        "pool_total_articles": pool_total,
        "pool_fresh_24h": pool_24h,
        "article_pool_source_type_distribution_1h": [
            {"source_type": row[0], "count": row[1]} for row in provider_dist
        ],
        "bocha_health_snapshot": bocha_snap,
        "zhipu_health_snapshot": zhipu_snap,
        "cards_detail": [
            {
                "section": c.get("section"),
                "category": c.get("category"),
                "title": c.get("title", "")[:80],
                "source_name": c.get("source_name"),
                "url": c.get("url", "")[:120],
                "language": c.get("language"),
                "has_image": bool(c.get("image_url")),
            }
            for c in cards
        ],
    }

    out_dir = ROOT / "experiments" / "search_v2" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "baseline_post_phase_a.json"
    out_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(flush=True)
    print(f"=== SAVED: {out_path} ({out_path.stat().st_size} bytes) ===", flush=True)

    # ── Step H: 关键字段打印 ───────────────────────────────
    print(flush=True)
    print("=== KEY BASELINE METRICS ===", flush=True)
    print(f"publish_grade: {baseline['report_publish_grade']}", flush=True)
    print(f"report_status: {baseline['report_status']}", flush=True)
    print(f"article_count: {baseline['report_item_count']}", flush=True)
    print(f"report_summary_length: {baseline['report_summary_length']}", flush=True)
    print(f"report_markdown_length: {baseline['report_markdown_length']}", flush=True)
    print(f"orchestrator_elapsed: {baseline['orchestrator_meta'].get('elapsed_seconds', '?')}s", flush=True)
    print(f"retrieval_duration_s: {baseline['retrieval_duration_seconds']}", flush=True)
    print(f"pool_total: {baseline['pool_total_articles']}", flush=True)
    print(f"pool_fresh_24h: {baseline['pool_fresh_24h']}", flush=True)
    print(flush=True)
    print(f"ingest_decision: {json.dumps(baseline['ingest_decision'], ensure_ascii=False)}", flush=True)
    print(flush=True)
    print(f"bocha_health.request_count: {bocha_snap['request_count']} (new instance, expected 0)", flush=True)
    print(f"bocha_health.state: {bocha_snap['state']}", flush=True)
    print(f"bocha_health.enabled: {bocha_snap['enabled']}", flush=True)
    print(flush=True)
    print(f"zhipu_health.request_count: {zhipu_snap['request_count']} (new instance, expected 0)", flush=True)
    print(f"zhipu_health.state: {zhipu_snap['state']}", flush=True)
    print(f"zhipu_health.enabled: {zhipu_snap['enabled']}", flush=True)
    print(flush=True)
    print(f"source_type distribution (past 1h): {baseline['article_pool_source_type_distribution_1h']}", flush=True)
    print(flush=True)
    print(f"cards_detail ({len(cards)} items):", flush=True)
    for i, c in enumerate(baseline["cards_detail"], 1):
        img_tag = "[IMG]" if c["has_image"] else "[no-img]"
        print(f"  {i}. [{c['section']}/{c['category']}] {c['title']} {img_tag}", flush=True)
        print(f"     source={c['source_name']} lang={c['language']}", flush=True)

    print(flush=True)
    print("=== RUN COMPLETE ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
