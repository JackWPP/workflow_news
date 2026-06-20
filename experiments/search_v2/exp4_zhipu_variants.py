"""实验 4: 智谱三变体搜索质量对比

目标: 验证 search_std / search_pro / search_pro_sogou 在中英文上的表现
- 同 6 query (CORE_SAMPLE)
- 三变体 × 2 freshness (oneWeek / noLimit) = 36 次调用
- 度量: 召回数、延迟、字段完整度、与 Bocha URL 重叠率

输出:
- results/exp4_raw.json
- results/exp4_summary.csv
- reports/exp4_report.md
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

ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ZHIPU_API_KEY = os.environ.get("ZHIPUAI_API_KEY", "").strip()
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

if not ZHIPU_API_KEY:
    sys.exit("ERROR: ZHIPUAI_API_KEY 未设置")

RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
from fixtures.queries import CORE_SAMPLE  # noqa: E402

ENGINES = ["search_std", "search_pro", "search_pro_sogou"]
FRESHNESS_LIST = ["oneWeek", "noLimit"]
COUNT = 50
INTER_CALL_DELAY = 0.5


async def call_zhipu(client, query: str, engine: str, freshness: str, count: int = 50) -> dict:
    """调智谱 web_search API."""
    payload = {
        "search_query": query,
        "search_engine": engine,
        "count": count,
        "search_recency_filter": freshness,
        "content_size": "high",
    }
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    start = time.perf_counter()
    try:
        resp = await client.post(ZHIPU_URL, json=payload, headers=headers, timeout=30.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code != 200:
            return {"ok": False, "error": f"http_{resp.status_code}: {resp.text[:200]}",
                    "latency_ms": latency_ms, "results": []}
        data = resp.json()
        search_result = data.get("search_result") or []
        return {"ok": True, "error": None, "latency_ms": latency_ms, "results": search_result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300],
                "latency_ms": int((time.perf_counter() - start) * 1000), "results": []}


def analyze_zhipu_results(results: list[dict]) -> dict:
    """分析智谱返回结果."""
    n = len(results)
    if n == 0:
        return {"n_results": 0, "n_with_content": 0, "n_with_publish": 0,
                "content_chars_p50": 0, "content_chars_avg": 0, "uniq_domains": 0}
    contents = [(r.get("content") or "") for r in results]
    content_lens = [len(c) for c in contents if c.strip()]
    n_with_content = sum(1 for c in contents if c.strip())
    n_with_publish = sum(1 for r in results if r.get("publish_date"))

    def pct(lst, p):
        if not lst: return 0
        s = sorted(lst)
        return s[min(len(s)-1, int(len(s)*p))]

    domains = set()
    for r in results:
        link = r.get("link") or ""
        if "://" in link:
            domains.add(link.split("/")[2])

    return {
        "n_results": n,
        "n_with_content": n_with_content,
        "n_with_publish": n_with_publish,
        "content_chars_p50": pct(content_lens, 0.5),
        "content_chars_avg": int(statistics.mean(content_lens)) if content_lens else 0,
        "uniq_domains": len(domains),
    }


def compute_overlap_with_bocha(zhipu_results: list[dict], bocha_urls: set[str]) -> float:
    """计算智谱结果与 Bocha 的 URL 重叠率."""
    zhipu_urls = {r.get("link") for r in zhipu_results if r.get("link")}
    overlap = zhipu_urls & bocha_urls
    return len(overlap) / len(zhipu_urls) if zhipu_urls else 0


def load_bocha_urls_from_exp1() -> dict[str, set[str]]:
    """从 exp1 拿每个 query 的 Bocha URL 集合."""
    exp1_data = json.loads((RESULTS_DIR / "exp1_raw.json").read_text(encoding="utf-8"))
    rows = exp1_data["rows"]
    bocha_urls = {}
    for q in dict.fromkeys(r["query"] for r in rows):
        candidates = [
            r for r in rows
            if r["query"] == q
            and r["param_count"] == 50
            and r["param_summary"] is True
            and r["param_freshness"] == "oneWeek"
        ]
        if not candidates:
            continue
        raw = candidates[0]["raw_results_truncated"]
        urls = {item.get("url") for item in raw if item.get("url")}
        bocha_urls[q] = urls
    return bocha_urls


async def main():
    print(f"=== exp 4: Zhipu three variants === queries: {len(CORE_SAMPLE)}")
    print(f"engines: {ENGINES}, freshness: {FRESHNESS_LIST}, count: {COUNT}")
    total_calls = len(CORE_SAMPLE) * len(ENGINES) * len(FRESHNESS_LIST)
    print(f"total calls: {total_calls}, est duration: {total_calls * 2:.0f}s")
    print()

    bocha_urls = load_bocha_urls_from_exp1()
    rows = []

    import httpx
    async with httpx.AsyncClient() as client:
        idx = 0
        for q_spec in CORE_SAMPLE:
            query = q_spec["query"]
            for engine in ENGINES:
                for freshness in FRESHNESS_LIST:
                    idx += 1
                    result = await call_zhipu(client, query, engine, freshness, COUNT)
                    metrics = analyze_zhipu_results(result["results"])
                    overlap_rate = compute_overlap_with_bocha(result["results"], bocha_urls.get(query, set()))
                    row = {
                        "idx": idx,
                        "query": query,
                        "language": q_spec["language"],
                        "section": q_spec["section"],
                        "engine": engine,
                        "freshness": freshness,
                        "ok": result["ok"],
                        "latency_ms": result["latency_ms"],
                        "error": result.get("error"),
                        "overlap_with_bocha": overlap_rate,
                        **metrics,
                    }
                    rows.append(row)
                    marker = "ok" if result["ok"] else "ERR"
                    print(f"  [{idx:2d}/{total_calls}] {marker} q={query[:18]:<18s} eng={engine:<18s} fr={freshness:<8s} "
                          f"n={metrics['n_results']:2d} content_p50={metrics['content_chars_p50']:4d} "
                          f"pub={metrics['n_with_publish']:2d}/{metrics['n_results']} "
                          f"overlap={overlap_rate:.0%} {result['latency_ms']:5d}ms")
                    await asyncio.sleep(INTER_CALL_DELAY)

    out_raw = RESULTS_DIR / "exp4_raw.json"
    out_raw.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_csv = RESULTS_DIR / "exp4_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"saved: {out_raw}")
    print(f"saved: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
