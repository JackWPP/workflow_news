"""智谱 link 字段稳定性深度探查 (避开 console 输出中文, 全部落盘 JSON)."""
import asyncio
import json
import os
import sys
from pathlib import Path

ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import httpx

ZHIPU_KEY = os.environ.get("ZHIPUAI_API_KEY", "").strip()
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


async def call(query, engine, count=10, freshness="oneWeek", content_size="high"):
    payload = {
        "search_query": query,
        "search_engine": engine,
        "count": count,
        "search_recency_filter": freshness,
        "content_size": content_size,
    }
    headers = {"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://open.bigmodel.cn/api/paas/v4/web_search", json=payload, headers=headers)
    except Exception as exc:
        return {"_error": f"exc: {type(exc).__name__}", "_body": str(exc)[:300]}
    if resp.status_code != 200:
        return {"_error": f"http_{resp.status_code}", "_body": resp.text[:500]}
    return resp.json()


QUERIES = [
    ("zh1", "注塑机 新品发布"),
    ("zh2", "塑料污染 治理 政策"),
    ("zh3", "高分子材料 研究进展"),
    ("en1", "polymer industry news"),
    ("en2", "biodegradable polymer research"),
    ("en3", "plastic recycling regulation"),
]
ENGINES = ["search_std", "search_pro", "search_pro_sogou"]


async def main():
    out = {"records": []}
    summary_lines = []
    for engine in ENGINES:
        for tag, q in QUERIES:
            data = await call(q, engine, count=10)
            if "_error" in data:
                line = f"engine={engine:<18s} q={tag} ERROR={data['_error']} body={data.get('_body','')[:80]}"
                print(line)
                summary_lines.append(line)
                out["records"].append({
                    "engine": engine, "query_tag": tag, "query": q,
                    "error": data["_error"], "body": data.get("_body", ""),
                    "n_results": 0, "empty_link": 0, "empty_title": 0,
                    "empty_content": 0, "avg_content_chars": 0,
                })
                await asyncio.sleep(0.6)
                continue
            results = data.get("search_result") or []
            empty_link = sum(1 for r in results if not (r.get("link") or "").strip())
            empty_title = sum(1 for r in results if not (r.get("title") or "").strip())
            empty_content = sum(1 for r in results if not (r.get("content") or "").strip())
            avg_content = (sum(len(r.get("content") or "") for r in results) // max(len(results), 1))

            line = f"engine={engine:<18s} q={tag} n={len(results):>2} empty_link={empty_link} empty_title={empty_title} avg_content={avg_content}"
            print(line)
            summary_lines.append(line)
            out["records"].append({
                "engine": engine,
                "query_tag": tag,
                "query": q,
                "n_results": len(results),
                "empty_link": empty_link,
                "empty_title": empty_title,
                "empty_content": empty_content,
                "avg_content_chars": avg_content,
                "raw_data": data,
            })
            await asyncio.sleep(0.6)

    fp = RESULTS_DIR / "zhipu_link_probe.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"saved: {fp}")

    # Aggregate summary
    print()
    print("=== Summary by engine ===")
    for engine in ENGINES:
        zh_recs = [r for r in out["records"] if r["engine"] == engine and r["query_tag"].startswith("zh")]
        en_recs = [r for r in out["records"] if r["engine"] == engine and r["query_tag"].startswith("en")]

        def agg(recs, label):
            total_n = sum(r["n_results"] for r in recs)
            total_empty = sum(r["empty_link"] for r in recs)
            avg_n = total_n / max(len(recs), 1)
            empty_pct = (total_empty / max(total_n, 1)) * 100
            return f"  {engine:<18s} {label}: avg_n={avg_n:.1f}, empty_link={total_empty}/{total_n} ({empty_pct:.0f}%)"

        print(agg(zh_recs, "zh"))
        print(agg(en_recs, "en"))


if __name__ == "__main__":
    asyncio.run(main())
