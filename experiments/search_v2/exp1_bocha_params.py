"""实验 1: Bocha 参数扫荡

目标: 数据驱动回答 4 个问题
1. count=10/30/50, 召回数和延迟的边际曲线
2. summary=true vs false, 字数分布、是否值得开
3. freshness=oneDay/oneWeek/oneMonth/noLimit, 召回差异
4. 单次成本（用余额查询前后差值估算）

输出:
- results/exp1_raw.json    (每次调用的原始数据)
- results/exp1_summary.csv (透视表)
- reports/exp1_report.md   (人类可读结论)

设计原则:
- 不依赖项目内代码, 直接调 Bocha HTTP, 避免与生产代码耦合
- BOCHA_API_KEY 从 .env 或环境变量读取
- 串行调用 + 0.3s 间隔, 避免触发限流
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.queries import CORE_SAMPLE  # noqa: E402

# 加载 .env
ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

BOCHA_API_KEY = os.environ.get("BOCHA_API_KEY", "").strip()
BOCHA_URL = "https://api.bochaai.com/v1/web-search"

if not BOCHA_API_KEY:
    sys.exit("ERROR: BOCHA_API_KEY 未设置, 请检查 .env")

RESULTS_DIR = Path(__file__).parent / "results"
REPORTS_DIR = Path(__file__).parent / "reports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

COUNTS = [10, 30, 50]
SUMMARIES = [True, False]
FRESHNESS_LIST = ["oneDay", "oneWeek", "oneMonth", "noLimit"]
INTER_CALL_DELAY = 0.3

async def call_bocha(client, query, count, summary, freshness):
    """单次调用; 返回 ok/latency_ms/raw_results/api_code/error."""
    payload = {"query": query, "count": count, "summary": summary, "freshness": freshness}
    headers = {"Authorization": f"Bearer {BOCHA_API_KEY}", "Content-Type": "application/json"}
    start = time.perf_counter()
    try:
        resp = await client.post(BOCHA_URL, json=payload, headers=headers, timeout=30.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code != 200:
            return {"ok": False, "latency_ms": latency_ms, "http_status": resp.status_code,
                    "error": f"http_{resp.status_code}: {resp.text[:200]}",
                    "raw_results": [], "api_code": None}
        data = resp.json()
        api_code = data.get("code")
        if api_code and api_code != 200:
            return {"ok": False, "latency_ms": latency_ms, "http_status": 200,
                    "api_code": api_code,
                    "error": f"api_code_{api_code}: {str(data.get('msg',''))[:200]}",
                    "raw_results": []}
        inner = data.get("data") or data
        web_pages = (inner.get("webPages") or {}).get("value") or []
        return {"ok": True, "latency_ms": latency_ms, "http_status": 200,
                "api_code": api_code, "raw_results": web_pages, "error": None}
    except Exception as exc:
        return {"ok": False, "latency_ms": int((time.perf_counter() - start) * 1000),
                "http_status": None, "api_code": None,
                "error": str(exc)[:300], "raw_results": []}


def analyze(raw_results):
    """提取关键度量."""
    n = len(raw_results)
    if n == 0:
        return {"n_results": 0, "n_with_summary": 0, "n_with_published": 0,
                "summary_chars_p50": 0, "summary_chars_p95": 0, "summary_chars_min": 0,
                "summary_chars_max": 0, "snippet_chars_p50": 0, "snippet_chars_avg": 0,
                "uniq_domains": 0}
    summaries = [(r.get("summary") or "") for r in raw_results]
    snippets = [(r.get("snippet") or "") for r in raw_results]
    summary_lens = [len(s) for s in summaries if s.strip()]
    snippet_lens = [len(s) for s in snippets]
    n_with_summary = sum(1 for s in summaries if s.strip())
    n_with_published = sum(1 for r in raw_results if r.get("datePublished"))

    def pct(lst, p):
        if not lst: return 0
        s = sorted(lst)
        return s[min(len(s)-1, int(len(s)*p))]

    domains = set()
    for r in raw_results:
        url = r.get("url") or ""
        if "://" in url:
            domains.add(url.split("/")[2])

    return {
        "n_results": n,
        "n_with_summary": n_with_summary,
        "n_with_published": n_with_published,
        "summary_chars_min": min(summary_lens) if summary_lens else 0,
        "summary_chars_max": max(summary_lens) if summary_lens else 0,
        "summary_chars_p50": pct(summary_lens, 0.5),
        "summary_chars_p95": pct(summary_lens, 0.95),
        "snippet_chars_p50": pct(snippet_lens, 0.5),
        "snippet_chars_avg": int(statistics.mean(snippet_lens)) if snippet_lens else 0,
        "uniq_domains": len(domains),
    }


async def query_balance(client):
    """查 Bocha 余额. 失败返回 None."""
    try:
        resp = await client.post(
            "https://api.bocha.cn/v1/billing/balance",
            headers={"Authorization": f"Bearer {BOCHA_API_KEY}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return (resp.json().get("data") or {}).get("remaining")
    except Exception as exc:
        print(f"  [warn] balance query failed: {exc}")
    return None

async def main():
    print(f"=== exp 1: Bocha param sweep ===")
    print(f"queries: {len(CORE_SAMPLE)}, params: count={COUNTS} x summary={SUMMARIES} x freshness={FRESHNESS_LIST}")
    total_calls = len(CORE_SAMPLE) * len(COUNTS) * len(SUMMARIES) * len(FRESHNESS_LIST)
    print(f"total calls: {total_calls}, est duration: {total_calls * 1.8:.0f}s")
    print()

    raw_records = []
    summary_rows = []

    async with httpx.AsyncClient() as client:
        balance_before = await query_balance(client)
        print(f"balance_before: {balance_before}")

        idx = 0
        for q in CORE_SAMPLE:
            for count in COUNTS:
                for summary_flag in SUMMARIES:
                    for freshness in FRESHNESS_LIST:
                        idx += 1
                        result = await call_bocha(client, q["query"], count, summary_flag, freshness)
                        metrics = analyze(result["raw_results"])
                        row = {
                            "idx": idx,
                            "query": q["query"],
                            "language": q["language"],
                            "section": q["section"],
                            "param_count": count,
                            "param_summary": summary_flag,
                            "param_freshness": freshness,
                            "ok": result["ok"],
                            "latency_ms": result["latency_ms"],
                            "error": result.get("error"),
                            **metrics,
                        }
                        summary_rows.append(row)
                        raw_records.append({
                            **row,
                            "raw_results": result["raw_results"],
                        })
                        marker = "ok" if result["ok"] else "ERR"
                        print(f"  [{idx:3d}/{total_calls}] {marker} q={q['query'][:18]:18s} c={count:3d} s={int(summary_flag)} fr={freshness:8s} -> n={metrics['n_results']:2d} sum_p50={metrics['summary_chars_p50']:4d} pub={metrics['n_with_published']:2d}/{metrics['n_results']} dom={metrics['uniq_domains']:2d} {result['latency_ms']:5d}ms")
                        await asyncio.sleep(INTER_CALL_DELAY)

        balance_after = await query_balance(client)
        print(f"balance_after: {balance_after}")

    cost_total = None
    cost_per_call = None
    if balance_before is not None and balance_after is not None:
        cost_total = round(balance_before - balance_after, 4)
        cost_per_call = round(cost_total / total_calls, 6) if total_calls else 0
        print(f"cost: total={cost_total}, per_call={cost_per_call}")

    out_raw = RESULTS_DIR / "exp1_raw.json"
    out_raw.write_text(json.dumps({
        "ts": datetime.now().isoformat(),
        "balance_before": balance_before,
        "balance_after": balance_after,
        "cost_total": cost_total,
        "cost_per_call": cost_per_call,
        "rows": raw_records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    out_csv = RESULTS_DIR / "exp1_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        if summary_rows:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)

    print(f"saved: {out_raw}")
    print(f"saved: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
