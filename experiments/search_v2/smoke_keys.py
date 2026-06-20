"""验证 Bocha + Zhipu 两个 key 是否可用."""
import asyncio
import os
import sys
import time
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

BOCHA_KEY = os.environ.get("BOCHA_API_KEY", "").strip()
ZHIPU_KEY = os.environ.get("ZHIPUAI_API_KEY", "").strip()

print(f"BOCHA_KEY: {BOCHA_KEY[:15]}... (len={len(BOCHA_KEY)})")
print(f"ZHIPU_KEY: {ZHIPU_KEY[:15]}... (len={len(ZHIPU_KEY)})")
print()


async def test_bocha():
    print("=== Bocha web-search ===")
    payload = {"query": "注塑机 新品发布", "count": 5, "summary": True, "freshness": "oneWeek"}
    headers = {"Authorization": f"Bearer {BOCHA_KEY}", "Content-Type": "application/json"}
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.bochaai.com/v1/web-search", json=payload, headers=headers)
    lat = int((time.perf_counter() - start) * 1000)
    print(f"  status: {resp.status_code} ({lat}ms)")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:300]}")
        return False
    data = resp.json()
    code = data.get("code")
    msg = data.get("msg") or ""
    inner = data.get("data") or data
    n = len((inner.get("webPages") or {}).get("value") or [])
    print(f"  api_code: {code}, msg: {msg[:80]}, results: {n}")
    return n > 0


async def test_bocha_rerank():
    print("=== Bocha rerank ===")
    payload = {
        "model": "gte-rerank",
        "query": "ESG report",
        "top_n": 2,
        "return_documents": True,
        "documents": [
            "Alibaba published its 2024 ESG report",
            "The weather in Beijing is sunny",
            "Carbon reduction plans are detailed in the report",
        ],
    }
    headers = {"Authorization": f"Bearer {BOCHA_KEY}", "Content-Type": "application/json"}
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.bochaai.com/v1/rerank", json=payload, headers=headers)
    lat = int((time.perf_counter() - start) * 1000)
    print(f"  status: {resp.status_code} ({lat}ms)")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:300]}")
        return False
    data = resp.json()
    code = data.get("code")
    inner = data.get("data") or data
    results = inner.get("results") or []
    print(f"  api_code: {code}, top_n results: {len(results)}")
    for r in results:
        print(f"    idx={r.get('index')} score={r.get('relevance_score'):.4f}")
    return len(results) > 0


async def test_zhipu():
    print("=== Zhipu web_search (search_std) ===")
    payload = {
        "search_query": "注塑机 新品发布",
        "search_engine": "search_std",
        "count": 5,
        "search_recency_filter": "oneWeek",
        "content_size": "high",
    }
    headers = {"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"}
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://open.bigmodel.cn/api/paas/v4/web_search", json=payload, headers=headers)
    lat = int((time.perf_counter() - start) * 1000)
    print(f"  status: {resp.status_code} ({lat}ms)")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:300]}")
        return False
    data = resp.json()
    results = data.get("search_result") or []
    print(f"  results: {len(results)}")
    if results:
        first = results[0]
        print(f"  fields: {list(first.keys())}")
        print(f"  first title: {first.get('title','')[:60]}")
        print(f"  first link: {first.get('link','')[:80]}")
        print(f"  first publish_date: {first.get('publish_date','(none)')}")
        print(f"  first content_len: {len(first.get('content',''))}")
    return len(results) > 0


async def main():
    b1 = await test_bocha()
    print()
    b2 = await test_bocha_rerank()
    print()
    z = await test_zhipu()
    print()
    print(f"Summary: bocha_search={b1}, bocha_rerank={b2}, zhipu={z}")


if __name__ == "__main__":
    asyncio.run(main())
