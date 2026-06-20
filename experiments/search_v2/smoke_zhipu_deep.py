"""深挖智谱响应字段——link 为空是稳定问题还是偶发？"""
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


async def call(query, engine, count=10, freshness="oneWeek", content_size="high"):
    payload = {
        "search_query": query,
        "search_engine": engine,
        "count": count,
        "search_recency_filter": freshness,
        "content_size": content_size,
    }
    headers = {"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://open.bigmodel.cn/api/paas/v4/web_search", json=payload, headers=headers)
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


async def main():
    queries = [
        ("注塑机 新品发布", "zh"),
        ("polymer industry news", "en"),
        ("塑料污染 治理 政策", "zh"),
        ("biodegradable polymer research", "en"),
    ]
    for engine in ["search_std", "search_pro", "search_pro_sogou"]:
        for q, lang in queries:
            print(f"\n--- engine={engine} q='{q}' ({lang}) ---")
            data = await call(q, engine, count=10)
            if not data:
                continue
            results = data.get("search_result") or []
            print(f"  total results: {len(results)}")
            empty_link = sum(1 for r in results if not (r.get("link") or "").strip())
            print(f"  empty link count: {empty_link}/{len(results)}")
            if results:
                # show first 3 results with link/title/publish_date
                for i, r in enumerate(results[:3]):
                    link = r.get("link") or "(empty)"
                    title = (r.get("title") or "")[:50]
                    pub = r.get("publish_date") or "(no date)"
                    content_len = len(r.get("content") or "")
                    print(f"    [{i+1}] link={link[:70]!r} title={title!r} pub={pub} content={content_len}c")
            # save full first response for inspection
            if engine == "search_pro" and q == queries[0][0]:
                Path("experiments/search_v2/results/zhipu_sample_response.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print("  saved full sample response to results/zhipu_sample_response.json")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
