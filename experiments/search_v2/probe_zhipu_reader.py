"""Probe zhipu reader raw body."""
import asyncio
import json
import os
import time
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import httpx

URLS_TO_TEST = [
    "https://www.compoundingworld.com/",
    "https://m.chinabgao.com/k/zsj/72712.html",
    "https://news.xnnews.com.cn/cysx/202606/t20260614_4843694.shtml",
    "https://foyotec.com/",
    "https://example.com/",
]


async def t():
    key = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("ZHIPU_API_KEY", "")
    print(f"key len={len(key)}")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for url in URLS_TO_TEST:
        print(f"\n--- {url} ---")
        payload = {"url": url, "timeout": 12, "return_format": "markdown"}
        s = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=25.0) as c:
                r = await c.post("https://open.bigmodel.cn/api/paas/v4/reader", json=payload, headers=headers)
        except Exception as exc:
            print(f"  EXC: {type(exc).__name__}: {exc}")
            continue
        elapsed = int((time.perf_counter() - s) * 1000)
        print(f"  status={r.status_code} elapsed={elapsed}ms body_size={len(r.text)}")
        if r.status_code == 200:
            body = r.json()
            print(f"  body keys: {list(body.keys())}")
            print(f"  body raw head: {r.text[:500]}")
        else:
            print(f"  body: {r.text[:400]}")


asyncio.run(t())
