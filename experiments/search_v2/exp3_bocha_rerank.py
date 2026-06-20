"""实验 3: Bocha Rerank API 收益验证

目标: 验证 /v1/rerank (gte-rerank) 是否值得 ¥0.005/次
- 取 exp1 的 50 条原始结果
- 调 rerank API 拿分数排序
- 对比: rerank top-10 vs Bocha 默认前 10 条
- 度量: 重叠率、域名权威度、summary 字数、延迟、成本

样本: 6 query × 1 组 (count=50, summary=true, oneWeek) = 6 次 rerank 调用

输出:
- results/exp3_raw.json
- results/exp3_summary.csv
- reports/exp3_report.md
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

BOCHA_API_KEY = os.environ.get("BOCHA_API_KEY", "").strip()
BOCHA_RERANK_URL = "https://api.bochaai.com/v1/rerank"

if not BOCHA_API_KEY:
    sys.exit("ERROR: BOCHA_API_KEY 未设置")

RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_exp1_50_results() -> list[dict]:
    """从 exp1 拿 6 query 的 50 条结果."""
    exp1_data = json.loads((RESULTS_DIR / "exp1_raw.json").read_text(encoding="utf-8"))
    rows = exp1_data["rows"]
    samples = []
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
        raw = candidates[0]["raw_results"]
        samples.append({
            "query": q,
            "language": candidates[0]["language"],
            "section": candidates[0]["section"],
            "raw_results": raw,
        })
    return samples


async def call_rerank(client, query: str, documents: list[str], top_n: int = 10) -> dict:
    """调 Bocha rerank API."""
    payload = {
        "model": "gte-rerank",
        "query": query,
        "top_n": top_n,
        "return_documents": True,
        "documents": documents,
    }
    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }
    start = time.perf_counter()
    try:
        resp = await client.post(BOCHA_RERANK_URL, json=payload, headers=headers, timeout=30.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code != 200:
            return {"ok": False, "error": f"http_{resp.status_code}", "latency_ms": latency_ms, "results": []}
        data = resp.json()
        api_code = data.get("code")
        if api_code and api_code != 200:
            return {"ok": False, "error": f"api_code_{api_code}", "latency_ms": latency_ms, "results": []}
        inner = data.get("data") or data
        results = inner.get("results") or []
        return {"ok": True, "error": None, "latency_ms": latency_ms, "results": results}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - start) * 1000), "results": []}


def analyze_overlap(default_top10: list[dict], rerank_top10: list[dict]) -> dict:
    """计算重叠率、域名权威度等."""
    default_urls = {r.get("url") for r in default_top10}
    rerank_urls = {r.get("url") for r in rerank_top10}
    overlap = default_urls & rerank_urls
    overlap_rate = len(overlap) / 10.0 if default_urls else 0

    # 域名权威度 (简化: 看 edu.cn / gov.cn / nature.com 等白名单)
    whitelist = {"edu.cn", "ac.cn", "cas.cn", "nature.com", "acs.org", "sciencedirect.com", "gov.cn"}
    def count_whitelist(urls):
        return sum(1 for u in urls if any(w in u for w in whitelist))

    default_wl = count_whitelist(default_urls)
    rerank_wl = count_whitelist(rerank_urls)

    # summary 字数
    default_summary_lens = [len(r.get("summary") or "") for r in default_top10]
    rerank_summary_lens = [len(r.get("summary") or "") for r in rerank_top10]

    return {
        "overlap_count": len(overlap),
        "overlap_rate": overlap_rate,
        "default_whitelist_hits": default_wl,
        "rerank_whitelist_hits": rerank_wl,
        "default_summary_avg": int(statistics.mean(default_summary_lens)) if default_summary_lens else 0,
        "rerank_summary_avg": int(statistics.mean(rerank_summary_lens)) if rerank_summary_lens else 0,
    }


async def main():
    samples = load_exp1_50_results()
    print(f"=== exp 3: Bocha rerank === samples: {len(samples)}")
    if not samples:
        print("ERROR: no samples from exp1")
        return

    import httpx
    rows = []
    async with httpx.AsyncClient() as client:
        for idx, sample in enumerate(samples, 1):
            query = sample["query"]
            raw = sample["raw_results"]
            if len(raw) < 10:
                print(f"  [{idx}] SKIP: only {len(raw)} results for '{query}'")
                continue

            # 默认 top-10
            default_top10 = raw[:10]

            # 准备 rerank documents (用 summary 或 snippet)
            documents = []
            for item in raw:
                text = item.get("summary") or item.get("snippet") or item.get("name") or ""
                documents.append(text[:500])  # 截断防超长

            # 调 rerank
            rerank_result = await call_rerank(client, query, documents, top_n=10)
            if not rerank_result["ok"]:
                print(f"  [{idx}] ERR: {rerank_result['error']}")
                continue

            # 提取 rerank top-10 (按 index 映射回 raw)
            rerank_top10 = []
            for r in rerank_result["results"]:
                idx_in_raw = r.get("index")
                if idx_in_raw is not None and 0 <= idx_in_raw < len(raw):
                    rerank_top10.append(raw[idx_in_raw])

            # 分析
            analysis = analyze_overlap(default_top10, rerank_top10)
            row = {
                "query": query,
                "language": sample["language"],
                "section": sample["section"],
                "n_raw": len(raw),
                "rerank_ok": rerank_result["ok"],
                "rerank_latency_ms": rerank_result["latency_ms"],
                "rerank_error": rerank_result["error"],
                **analysis,
            }
            rows.append(row)
            print(f"  [{idx}] q={query[:20]:<20s} overlap={analysis['overlap_rate']:.0%} "
                  f"wl: {analysis['default_whitelist_hits']}→{analysis['rerank_whitelist_hits']} "
                  f"sum: {analysis['default_summary_avg']}→{analysis['rerank_summary_avg']} "
                  f"{rerank_result['latency_ms']}ms")

    out_raw = RESULTS_DIR / "exp3_raw.json"
    out_raw.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_csv = RESULTS_DIR / "exp3_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"saved: {out_raw}")
    print(f"saved: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
