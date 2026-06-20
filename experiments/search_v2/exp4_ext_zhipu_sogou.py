"""实验 4 拓展: 智谱 search_pro_sogou 的 count/freshness 扫荡 + 与 Bocha URL 重叠率

锁定使用 search_pro_sogou (实验 4 已确认 std/pro 中文 link 100% 损坏)
扫荡参数:
- count: 10 / 30 / 50  (智谱文档说 1-50)
- freshness: oneDay / oneWeek / oneMonth / noLimit

度量:
- 实际召回数 (是否兑现 count)
- 延迟
- content 字数分布
- publish_date 覆盖率
- 与 Bocha 在同 query 上的 URL 重叠率 (从 exp1_raw.json 拿 Bocha URL)

样本: 6 个 CORE_SAMPLE query × 3 count × 4 freshness = 72 次智谱调用
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from fixtures.queries import CORE_SAMPLE  # noqa: E402

ZHIPU_KEY = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

if not ZHIPU_KEY:
    sys.exit("ERROR: ZHIPUAI_API_KEY 未设置")

RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ENGINE = "search_pro_sogou"
COUNTS = [10, 30, 50]
FRESHNESS_LIST = ["oneDay", "oneWeek", "oneMonth", "noLimit"]
INTER_CALL_DELAY = 0.8


import httpx


async def call(client, query, count, freshness):
    payload = {
        "search_query": query,
        "search_engine": ENGINE,
        "count": count,
        "search_recency_filter": freshness,
        "content_size": "high",
    }
    headers = {"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"}
    start = time.perf_counter()
    try:
        resp = await client.post(ZHIPU_URL, json=payload, headers=headers, timeout=30.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "latency_ms": int((time.perf_counter()-start)*1000), "results": []}
    if resp.status_code != 200:
        return {"ok": False, "error": f"http_{resp.status_code}: {resp.text[:200]}",
                "latency_ms": latency_ms, "results": []}
    data = resp.json()
    return {"ok": True, "error": None, "latency_ms": latency_ms,
            "results": data.get("search_result") or []}


def analyze(results):
    n = len(results)
    if n == 0:
        return {"n_results": 0, "n_with_link": 0, "n_with_publish": 0,
                "content_p50": 0, "content_avg": 0, "uniq_domains": 0}
    n_with_link = sum(1 for r in results if (r.get("link") or "").strip())
    n_with_publish = sum(1 for r in results if r.get("publish_date"))
    content_lens = [len(r.get("content") or "") for r in results]

    def pct(lst, p):
        if not lst: return 0
        s = sorted(lst)
        return s[min(len(s)-1, int(len(s)*p))]

    domains = set()
    for r in results:
        link = (r.get("link") or "").strip()
        if "://" in link:
            domains.add(link.split("/")[2])
    return {
        "n_results": n,
        "n_with_link": n_with_link,
        "n_with_publish": n_with_publish,
        "content_p50": pct(content_lens, 0.5),
        "content_avg": int(statistics.mean(content_lens)) if content_lens else 0,
        "uniq_domains": len(domains),
    }


def load_bocha_urls(query):
    """从 exp1_raw.json 拿这个 query 在 count=50/oneWeek/summary=true 下的 URL 集合."""
    fp = RESULTS_DIR / "exp1_raw.json"
    if not fp.exists():
        return set()
    data = json.loads(fp.read_text(encoding="utf-8"))
    for r in data.get("rows", []):
        if (r.get("query") == query
            and r.get("param_count") == 50
            and r.get("param_summary") is True
            and r.get("param_freshness") == "oneWeek"
            and r.get("ok")):
            raw = r.get("raw_results") or r.get("raw_results_truncated") or []
            return {item.get("url") for item in raw if item.get("url")}
    return set()


async def main():
    print(f"=== exp4_ext: Zhipu search_pro_sogou param sweep ===")
    print(f"queries: {len(CORE_SAMPLE)}, params: count={COUNTS} x freshness={FRESHNESS_LIST}")
    total = len(CORE_SAMPLE) * len(COUNTS) * len(FRESHNESS_LIST)
    print(f"total calls: {total}, est duration: {total*1.5:.0f}s")
    print()

    raw_records = []
    summary_rows = []
    bocha_url_cache = {}

    async with httpx.AsyncClient() as client:
        idx = 0
        for q in CORE_SAMPLE:
            query = q["query"]
            if query not in bocha_url_cache:
                bocha_url_cache[query] = load_bocha_urls(query)
            bocha_urls = bocha_url_cache[query]
            for count in COUNTS:
                for freshness in FRESHNESS_LIST:
                    idx += 1
                    result = await call(client, query, count, freshness)
                    metrics = analyze(result["results"])

                    # URL 重叠率
                    zhipu_urls = {r.get("link") for r in result["results"] if (r.get("link") or "").strip()}
                    overlap = zhipu_urls & bocha_urls
                    overlap_rate = len(overlap) / len(zhipu_urls) if zhipu_urls else 0.0

                    row = {
                        "idx": idx,
                        "query": query,
                        "language": q["language"],
                        "section": q["section"],
                        "engine": ENGINE,
                        "param_count": count,
                        "param_freshness": freshness,
                        "ok": result["ok"],
                        "latency_ms": result["latency_ms"],
                        "error": result.get("error"),
                        "bocha_url_count": len(bocha_urls),
                        "overlap_with_bocha": round(overlap_rate, 3),
                        **metrics,
                    }
                    summary_rows.append(row)
                    raw_records.append({**row, "raw_results": result["results"]})

                    marker = "ok" if result["ok"] else "ERR"
                    print(f"  [{idx:2d}/{total}] {marker} q={query[:18]:<18s} c={count:3d} fr={freshness:<8s} "
                          f"n={metrics['n_results']:2d} link={metrics['n_with_link']:2d} "
                          f"pub={metrics['n_with_publish']:2d}/{metrics['n_results']} "
                          f"content_p50={metrics['content_p50']:5d} "
                          f"overlap={overlap_rate:.0%} {result['latency_ms']:5d}ms")
                    await asyncio.sleep(INTER_CALL_DELAY)

    out_raw = RESULTS_DIR / "exp4_ext_raw.json"
    out_raw.write_text(json.dumps({"rows": raw_records}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_csv = RESULTS_DIR / "exp4_ext_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        if summary_rows:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)
    print(f"saved: {out_raw}")
    print(f"saved: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
